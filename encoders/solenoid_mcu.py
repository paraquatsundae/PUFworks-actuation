"""SolenoidFrameV1 encoder over USB serial to boom MCU."""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Callable, Optional

try:
    import serial  # pyserial

    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


class MockTransport:
    """Captures writes for bench tests — no hardware."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.open = True

    def write(self, data: bytes) -> bool:
        self.writes.append(data)
        return True

    def close(self) -> None:
        self.open = False


class SerialTransport:
    """Blocking pyserial writer owned by the encoder thread."""

    def __init__(self, port: str, baud: int, log: Callable[[str], None]) -> None:
        self.port = port
        self.baud = baud
        self._log = log
        self._serial = None
        self.open = False

    def ensure_open(self) -> bool:
        if self._serial is not None:
            return True
        if not HAS_SERIAL:
            self._log("pyserial not installed — pip install pyserial")
            return False
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=0)
            self.open = True
            self._log(f"Serial open {self.port} @ {self.baud}")
            return True
        except Exception as e:  # noqa: BLE001 — bench path
            self._log(f"Serial open failed {self.port}: {e}")
            self._serial = None
            self.open = False
            return False

    def write(self, data: bytes) -> bool:
        if not self.ensure_open():
            return False
        try:
            self._serial.write(data)
            self._serial.flush()
            return True
        except Exception as e:  # noqa: BLE001
            self._log(f"Serial write failed: {e}")
            self.close()
            return False

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self.open = False


class SolenoidMcuEncoder:
    """Build SolenoidFrameV1 lines and dispatch on a drop-old writer thread."""

    LINE_PREFIX = "SOLENOID:"

    def __init__(
        self,
        profile: dict,
        log: Callable[[str], None],
        validate_solenoid_frame_v1,
        validate_actuator_profile_v1,
    ) -> None:
        validate_actuator_profile_v1(profile)
        if profile["encoder"] != "solenoid_mcu":
            raise ValueError(f"profile encoder must be solenoid_mcu, got {profile['encoder']!r}")

        self.profile = profile
        self._log = log
        self._validate_frame = validate_solenoid_frame_v1
        self.section_count = int(profile["section_count"])
        self._section_map = profile.get("section_map", {})
        self._seq = 0
        self._last_mask = -1
        self._frames_sent = 0
        self._frames_suppressed = 0
        self._last_tx_time = 0.0
        self._link_state = "closed"
        self._last_error = ""

        transport = profile.get("transport", {})
        ttype = transport.get("type", "usb_serial")
        if ttype == "mock":
            self._transport: MockTransport | SerialTransport = MockTransport()
            self._link_state = "mock"
        elif ttype == "usb_serial":
            self._transport = SerialTransport(
                port=str(transport["port"]),
                baud=int(transport.get("baud", 115200)),
                log=log,
            )
        else:
            raise ValueError(f"unsupported transport.type {ttype!r} for solenoid_mcu")

        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=1)
        self._running = True
        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._thread.start()

    @property
    def mock_writes(self) -> list[bytes]:
        if isinstance(self._transport, MockTransport):
            return self._transport.writes
        return []

    def stop(self) -> None:
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._transport.close()
        self._link_state = "closed"

    def map_section_mask(self, bitmap: int) -> int:
        shift = int(self._section_map.get("shift", 0))
        mapped = (int(bitmap) >> shift) & ((1 << self.section_count) - 1)
        if self._section_map.get("invert"):
            mapped ^= (1 << self.section_count) - 1
        return mapped

    def build_frame(self, mask: int, *, hb: int = 1) -> dict:
        duty = [100 if (mask >> i) & 1 else 0 for i in range(self.section_count)]
        frame = {
            "schema": "SolenoidFrameV1",
            "seq": self._seq,
            "mask": f"0x{mask:X}",
            "duty": duty,
            "hb": int(hb),
        }
        self._validate_frame(frame)
        return frame

    def dispatch(self, mask: int, *, allow_tx: bool) -> dict:
        """Build frame for mask; send only when allow_tx is True (SECTION+ARM)."""
        mapped = self.map_section_mask(mask)
        frame = self.build_frame(mapped, hb=1)
        line = self.LINE_PREFIX + json.dumps(frame, separators=(",", ":")) + "\n"

        if not allow_tx:
            self._frames_suppressed += 1
            if mapped != self._last_mask:
                self._log(
                    f"SHADOW solenoid frame seq={frame['seq']} mask={frame['mask']} "
                    f"(TX suppressed)"
                )
                self._last_mask = mapped
            return frame

        self._seq += 1
        self._enqueue(line)
        self._frames_sent += 1
        if mapped != self._last_mask:
            self._log(f"SOLENOID TX seq={frame['seq']} mask={frame['mask']}")
            self._last_mask = mapped
        return frame

    def get_status(self) -> dict:
        return {
            "encoder": "solenoid_mcu",
            "link_state": self._link_state,
            "last_error": self._last_error or None,
            "frames_sent": self._frames_sent,
            "frames_suppressed": self._frames_suppressed,
            "last_tx_age_s": round(time.time() - self._last_tx_time, 3)
            if self._last_tx_time
            else None,
            "profile_name": self.profile.get("name"),
            "transport_type": self.profile.get("transport", {}).get("type"),
        }

    def _enqueue(self, line: str) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(line)
        except queue.Full:
            pass

    def _writer_loop(self) -> None:
        while self._running:
            try:
                line = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                break
            ok = self._transport.write(line.encode("ascii"))
            if ok:
                self._last_tx_time = time.time()
                if isinstance(self._transport, MockTransport):
                    self._link_state = "mock"
                elif self._transport.open:
                    self._link_state = "open"
            else:
                self._link_state = "error"
                self._last_error = "transport write failed"


def create_encoder(profile: dict, log: Callable[[str], None], contracts) -> SolenoidMcuEncoder:
    name = profile.get("encoder")
    if name == "solenoid_mcu":
        return SolenoidMcuEncoder(
            profile,
            log,
            contracts.validate_solenoid_frame_v1,
            contracts.validate_actuator_profile_v1,
        )
    raise ValueError(f"no encoder implementation for {name!r}")
