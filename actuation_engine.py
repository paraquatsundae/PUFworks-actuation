# PUFworks Actuation Gateway — Phase 1
#
# Direct-actuation sidecar: ingests SectionBitmapV1, applies the same staleness
# and authority rules as PUFworks-isobus, dispatches to hardware encoders (Phase 3+).
#
# Wire protocol (stdin):
#   VISION_BITMAP:{SectionBitmapV1 JSON}   10–20 Hz from vision / integrator
#   SECTION_BITMAP:{...}                   alias accepted for bench harnesses
#   SET_CONTROL_AUTHORITY:SHADOW|SECTION|…
#   ARM / DISARM
#   UI_HEARTBEAT                           1 Hz from UI host while armed
#   SET_SPEED:<km/h>                       bench speed interlock input
#   SET_SECTION_BITMAP:<hex>               manual bench vector (no vision)
#   SET_ACTUATOR_PROFILE:<name>            profile name (encoder Phase 3+)
#
# stdout:
#   TELEMETRY:{ActuationTelemetryV1 JSON}  ~10 Hz
#   [ACTUATION_LOG] …

from __future__ import annotations

import json
import os
import sys
import threading
import time

from contract_import import load as _load_contracts

try:
    from encoders import create_encoder
except ImportError:
    create_encoder = None  # type: ignore[misc, assignment]

_contracts = _load_contracts()
SECTION_BITMAP_STALE_MS = _contracts.SECTION_BITMAP_STALE_MS
UI_HEARTBEAT_TIMEOUT_S = _contracts.UI_HEARTBEAT_TIMEOUT_S
validate_section_bitmap_v1 = _contracts.validate_section_bitmap_v1
ContractError = _contracts.ContractError
validate_actuator_profile_v1 = _contracts.validate_actuator_profile_v1
validate_solenoid_frame_v1 = _contracts.validate_solenoid_frame_v1

TICK_HZ = 10.0
TICK_S = 1.0 / TICK_HZ


class ActuationController:
    AUTHORITY_ORDER = {
        "OBSERVE": 0,
        "ANNOUNCE": 1,
        "SHADOW": 2,
        "RATE_ONLY": 3,
        "SECTION": 4,
        "FULL": 5,
    }

    def __init__(self):
        self.control_authority = "OBSERVE"
        self.armed = False
        self.speed_kmh = 0.0
        self.speed_interlock = True
        self.min_ground_speed_kmh = 0.5
        self.ui_watchdog_enabled = True
        self.ui_watchdog_timeout = UI_HEARTBEAT_TIMEOUT_S
        self.last_ui_heartbeat = 0.0
        self.last_demote_reason = ""
        self.interlock_status = {"speed": True, "ui": True, "vision": True}

        self.vision_bitmap = 0
        self.vision_seq = -1
        self.vision_last_rx = 0.0
        self.vision_seen = False
        self.vision_source = ""
        self.num_boom_sections = 10
        self.manual_section_bitmap = None

        self.section_bitmap = 0
        self.actuator_profile = "none"
        self.encoder = None
        self.output_counts = {"section": 0, "rate": 0}
        self._last_shadow_logged = -1
        self._last_dispatched_bitmap = -1

        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, "actuation.log")

    def log(self, msg: str) -> None:
        line = f"[ACTUATION_LOG] {msg}"
        print(line, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _authority_level(self) -> int:
        return self.AUTHORITY_ORDER.get(self.control_authority, 0)

    def set_control_authority(self, rung: str) -> bool:
        rung = str(rung).upper()
        if rung not in self.AUTHORITY_ORDER:
            self.log(f"Rejected unknown control authority '{rung}'")
            return False
        prev = self.control_authority
        self.control_authority = rung
        if self.AUTHORITY_ORDER[rung] < self.AUTHORITY_ORDER["RATE_ONLY"]:
            self.armed = False
        self.log(f"Control authority {prev} -> {rung} (armed={self.armed})")
        return True

    def set_armed(self, armed: bool) -> bool:
        armed = bool(armed)
        if armed and self._authority_level() < self.AUTHORITY_ORDER["SECTION"]:
            self.log("ARM refused: raise authority to SECTION or FULL first")
            return False
        self.armed = armed
        self.log(f"{'ARMED' if armed else 'DISARMED'} at authority {self.control_authority}")
        return True

    def ui_heartbeat(self) -> None:
        self.last_ui_heartbeat = time.time()

    def set_speed_kmh(self, speed: float) -> None:
        self.speed_kmh = max(0.0, float(speed))

    def load_actuator_profile(self, name: str) -> bool:
        if create_encoder is None:
            self.log("Encoder package unavailable")
            return False
        profiles_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
        stem = name.strip()
        if stem.lower().endswith(".json"):
            stem = stem[:-5]
        path = None
        for cand in (stem + ".json", stem):
            p = os.path.join(profiles_dir, cand)
            if os.path.isfile(p):
                path = p
                break
        if path is None:
            self.log(f"Actuator profile not found: {name!r} (looked in {profiles_dir})")
            return False
        try:
            with open(path, encoding="utf-8") as f:
                profile = json.load(f)
            validate_actuator_profile_v1(profile)
        except (OSError, json.JSONDecodeError, ContractError) as e:
            self.log(f"Actuator profile load failed: {e}")
            return False
        if self.encoder is not None:
            self.encoder.stop()
            self.encoder = None
        try:
            self.encoder = create_encoder(profile, self.log, _contracts)
        except ValueError as e:
            self.log(f"Encoder init failed: {e}")
            return False
        self.actuator_profile = str(profile.get("name", stem))
        self.num_boom_sections = int(profile["section_count"])
        self.log(f"Actuator profile loaded: {self.actuator_profile} from {os.path.basename(path)}")
        return True

    def set_actuator_profile(self, name: str) -> bool:
        if name in ("none", "", "off"):
            if self.encoder is not None:
                self.encoder.stop()
                self.encoder = None
            self.actuator_profile = "none"
            self.log("Actuator profile cleared")
            return True
        return self.load_actuator_profile(name)

    def set_manual_section_bitmap(self, bitmap: int) -> bool:
        self.manual_section_bitmap = int(bitmap) & 0xFFFFFFFF
        if self._vision_fresh():
            self.log(
                f"Manual section bitmap {hex(self.manual_section_bitmap)} stored, "
                "but a live vision feed is fresh and takes priority."
            )
        else:
            self.log(f"Manual section bitmap -> {hex(self.manual_section_bitmap)} (bench vector)")
        return True

    def _vision_fresh(self) -> bool:
        return self.vision_seen and (
            (time.time() - self.vision_last_rx) * 1000.0 <= SECTION_BITMAP_STALE_MS
        )

    def _interlocks_ok(self) -> bool:
        speed_ok = (not self.speed_interlock) or (self.speed_kmh >= self.min_ground_speed_kmh)
        ui_ok = (
            (not self.ui_watchdog_enabled)
            or self.last_ui_heartbeat == 0.0
            or (time.time() - self.last_ui_heartbeat <= self.ui_watchdog_timeout)
        )
        vision_ok = (not self.vision_seen) or self._vision_fresh()
        self.interlock_status = {"speed": speed_ok, "ui": ui_ok, "vision": vision_ok}
        return speed_ok and ui_ok and vision_ok

    def _output_allowed(self, kind: str) -> bool:
        level = self._authority_level()
        if kind == "rate":
            return self.armed and level >= self.AUTHORITY_ORDER["RATE_ONLY"]
        if kind == "section":
            return self.armed and level >= self.AUTHORITY_ORDER["SECTION"]
        return False

    def ingest_vision_bitmap(self, msg: dict) -> bool:
        try:
            validate_section_bitmap_v1(msg)
            section_count = int(msg["section_count"])
            bitmap = int(str(msg["bitmap"]), 16)
            seq = int(msg.get("seq", 0))
            if self.vision_seen and seq <= self.vision_seq:
                self.log(f"VISION_BITMAP seq regression: {self.vision_seq} -> {seq}")
            self.vision_seq = seq
            self.vision_bitmap = bitmap
            self.vision_source = str(msg.get("source", ""))
            self.vision_last_rx = time.time()
            if not self.vision_seen:
                self.vision_seen = True
                self.num_boom_sections = section_count
                self.log(
                    f"Vision feed connected: source={self.vision_source} sections={section_count}"
                )
            return True
        except ContractError as e:
            self.log(f"VISION_BITMAP contract error: {e}")
            return False
        except (KeyError, ValueError, TypeError) as e:
            self.log(f"VISION_BITMAP parse error: {e}")
            return False

    def update_sections_from_inputs(self) -> None:
        """Resolve section_bitmap from vision or manual bench vector."""
        if self.vision_seen:
            if self._vision_fresh():
                self.section_bitmap = self.vision_bitmap
            else:
                self.section_bitmap = 0
        elif self.manual_section_bitmap is not None:
            self.section_bitmap = self.manual_section_bitmap
        else:
            self.section_bitmap = 0

    def _service_watchdogs(self) -> None:
        self._interlocks_ok()
        if not self.armed:
            return
        fault = [k for k in ("ui", "vision") if not self.interlock_status.get(k, True)]
        if fault:
            self.last_demote_reason = f"{', '.join(fault)} lost"
            self.log(f"WATCHDOG auto-demote to SHADOW ({self.last_demote_reason})")
            self.armed = False
            self.control_authority = "SHADOW"

    def _dispatch_output(self) -> None:
        out = self.section_bitmap if self._interlocks_ok() else 0
        allow_tx = self._output_allowed("section")

        if self.encoder is not None:
            sent_before = self.encoder.get_status().get("frames_sent", 0)
            self.encoder.dispatch(out, allow_tx=allow_tx)
            if allow_tx and self.encoder.get_status().get("frames_sent", 0) > sent_before:
                self.output_counts["section"] += 1
            return

        if not allow_tx:
            if self._authority_level() <= self.AUTHORITY_ORDER["SHADOW"] and out != self._last_shadow_logged:
                self.log(f"SHADOW section mask 0x{out:X} (output suppressed)")
                self._last_shadow_logged = out
            return

        self.output_counts["section"] += 1
        if out != self._last_dispatched_bitmap:
            self.log(f"OUTPUT section mask 0x{out:X} (no encoder profile loaded)")
            self._last_dispatched_bitmap = out

    def tick(self) -> None:
        self._service_watchdogs()
        self.update_sections_from_inputs()
        self._dispatch_output()


def build_telemetry(ctrl: ActuationController) -> dict:
    payload = {
        "schema": "ActuationTelemetryV1",
        "ts_ms": int(time.time() * 1000),
        "control_authority": ctrl.control_authority,
        "control_armed": ctrl.armed,
        "control_interlocks": dict(ctrl.interlock_status),
        "control_demote_reason": ctrl.last_demote_reason or None,
        "section_bitmap": f"0x{ctrl.section_bitmap:X}",
        "vision_bitmap": f"0x{ctrl.vision_bitmap:X}",
        "vision_seen": ctrl.vision_seen,
        "vision_fresh": ctrl._vision_fresh(),
        "vision_seq": ctrl.vision_seq,
        "vision_source": ctrl.vision_source,
        "num_boom_sections": ctrl.num_boom_sections,
        "speed_kmh": round(ctrl.speed_kmh, 2),
        "actuator_profile": ctrl.actuator_profile,
        "output_counts": dict(ctrl.output_counts),
    }
    if ctrl.encoder is not None:
        payload["encoder_status"] = ctrl.encoder.get_status()
    return payload


def _parse_int(text: str) -> int:
    text = text.strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def _ingest_bitmap_line(ctrl: ActuationController, payload: str) -> None:
    try:
        ctrl.ingest_vision_bitmap(json.loads(payload))
    except json.JSONDecodeError as e:
        ctrl.log(f"VISION_BITMAP invalid JSON: {e}")


def handle_command(ctrl: ActuationController, line: str) -> None:
    line = line.strip()
    if not line:
        return

    if line.startswith("VISION_BITMAP:"):
        _ingest_bitmap_line(ctrl, line[len("VISION_BITMAP:"):])
        return
    if line.startswith("SECTION_BITMAP:"):
        _ingest_bitmap_line(ctrl, line[len("SECTION_BITMAP:"):])
        return

    if line != "UI_HEARTBEAT":
        print(f"Actuation engine received command: {line}", flush=True)

    if line.startswith("SET_CONTROL_AUTHORITY:"):
        parts = line.split(":", 1)
        if len(parts) == 2:
            ctrl.set_control_authority(parts[1].strip())
    elif line == "ARM":
        ctrl.set_armed(True)
    elif line == "DISARM":
        ctrl.set_armed(False)
    elif line.startswith("UI_HEARTBEAT"):
        ctrl.ui_heartbeat()
    elif line.startswith("SET_SPEED:"):
        parts = line.split(":", 1)
        if len(parts) == 2:
            try:
                ctrl.set_speed_kmh(float(parts[1]))
            except ValueError:
                print(f"Invalid SET_SPEED value: {parts[1]}", flush=True)
    elif line.startswith("SET_SPEED_INTERLOCK:"):
        parts = line.split(":", 1)
        if len(parts) == 2:
            try:
                ctrl.speed_interlock = bool(int(parts[1]))
            except ValueError:
                pass
    elif line.startswith("SET_SECTION_BITMAP:"):
        parts = line.split(":", 1)
        if len(parts) == 2:
            try:
                ctrl.set_manual_section_bitmap(_parse_int(parts[1]))
            except ValueError:
                print(f"Invalid SET_SECTION_BITMAP value: {parts[1]}", flush=True)
    elif line.startswith("SET_ACTUATOR_PROFILE:"):
        parts = line.split(":", 1)
        if len(parts) == 2:
            ctrl.set_actuator_profile(parts[1].strip())
    else:
        ctrl.log(f"REJECTED out-of-scope command: {line.split(':')[0]}")


def main() -> None:
    print(
        "PUFworks Actuation Gateway started. Phase 3 — ingest + solenoid encoder.",
        flush=True,
    )
    ctrl = ActuationController()
    tick_lock = threading.Lock()

    def tick_loop() -> None:
        while True:
            t0 = time.time()
            with tick_lock:
                ctrl.tick()
            elapsed = time.time() - t0
            time.sleep(max(0.0, TICK_S - elapsed))

    def telemetry_loop() -> None:
        while True:
            try:
                with tick_lock:
                    payload = build_telemetry(ctrl)
                print(f"TELEMETRY:{json.dumps(payload)}", flush=True)
            except Exception as e:
                print(f"ERROR: telemetry_loop: {e}", flush=True)
            time.sleep(TICK_S)

    threading.Thread(target=tick_loop, daemon=True).start()
    threading.Thread(target=telemetry_loop, daemon=True).start()

    for line in sys.stdin:
        with tick_lock:
            handle_command(ctrl, line)


if __name__ == "__main__":
    main()
