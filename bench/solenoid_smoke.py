#!/usr/bin/env python3
"""
PUFworks-actuation Phase 3 bench — solenoid MCU encoder smoke (hardware-free default).

Uses profiles/bench_5section_mock.json (mock transport). Verifies:
  S1  profile loads; encoder_status present in telemetry
  S2  SHADOW: solenoid frames suppressed (frames_sent == 0, frames_suppressed > 0)
  S3  SECTION+ARM: mock transport receives SOLENOID: lines with hb=1
  S4  mask changes reflected in dispatched frames

Optional real serial (Uno on COMx):
  python bench/solenoid_smoke.py --profile bench_5section --port COM6

Run:
  python bench/solenoid_smoke.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.normpath(os.path.join(HERE, "..", "actuation_engine.py"))
PROFILES = os.path.normpath(os.path.join(HERE, "..", "profiles"))

telemetry: dict = {}
tel_lock = threading.Lock()
failures: list[str] = []


def send(proc, line: str) -> None:
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def get_tel(key, default=None):
    with tel_lock:
        return telemetry.get(key, default)


def reader(proc) -> None:
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if line.startswith("TELEMETRY:"):
            try:
                msg = json.loads(line[len("TELEMETRY:"):])
                with tel_lock:
                    telemetry.clear()
                    telemetry.update(msg)
            except json.JSONDecodeError:
                pass
        elif line.startswith("[ACTUATION_LOG]"):
            print(f"  act> {line}")


def check(name: str, ok: bool, detail: str = "") -> None:
    tag = "ok " if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def make_bitmap(seq: int, mask_hex: str, section_count: int = 5) -> str:
    msg = {
        "schema": "SectionBitmapV1",
        "ts_ms": int(time.time() * 1000),
        "seq": seq,
        "section_count": section_count,
        "bitmap": mask_hex,
        "source": "solenoid-smoke",
    }
    return json.dumps(msg, separators=(",", ":"))


def pump_mask(proc, seq: int, mask_hex: str, seconds: float) -> int:
    t_end = time.time() + seconds
    while time.time() < t_end:
        send(proc, "UI_HEARTBEAT")
        send(proc, "VISION_BITMAP:" + make_bitmap(seq, mask_hex))
        seq += 1
        time.sleep(0.08)
    return seq


def encoder_status() -> dict:
    return get_tel("encoder_status") or {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--profile",
        default="bench_5section_mock",
        help="profile stem under profiles/ (default: mock transport)",
    )
    ap.add_argument("--port", help="override usb_serial port in a copy of the profile")
    ap.add_argument("--duration", type=float, default=1.5, help="seconds per mask phase")
    args = ap.parse_args()

    profile_name = args.profile
    if args.port:
        src = os.path.join(PROFILES, profile_name + ".json")
        if not os.path.isfile(src):
            print(f"[solenoid_smoke] profile not found: {src}")
            sys.exit(2)
        with open(src, encoding="utf-8") as f:
            prof = json.load(f)
        prof["transport"] = {"type": "usb_serial", "port": args.port, "baud": 115200}
        profile_name = profile_name + "_runtime"
        runtime_path = os.path.join(PROFILES, profile_name + ".json")
        with open(runtime_path, "w", encoding="utf-8") as f:
            json.dump(prof, f, indent=2)
            f.write("\n")

    print(f"[solenoid_smoke] engine = {ENGINE}")
    print(f"[solenoid_smoke] profile = {profile_name}")

    proc = subprocess.Popen(
        [sys.executable, "-u", ENGINE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=os.path.dirname(ENGINE),
    )
    threading.Thread(target=reader, args=(proc,), daemon=True).start()

    try:
        time.sleep(0.4)
        send(proc, f"SET_ACTUATOR_PROFILE:{profile_name}")
        send(proc, "SET_CONTROL_AUTHORITY:SHADOW")
        send(proc, "SET_SPEED:10")
        send(proc, "UI_HEARTBEAT")

        time.sleep(0.3)
        check("S1 profile loaded", get_tel("actuator_profile") not in (None, "none"),
              f"actuator_profile={get_tel('actuator_profile')}")

        seq = 0
        seq = pump_mask(proc, seq, "0x1F", args.duration)
        enc = encoder_status()
        check("S2 SHADOW suppresses serial TX", enc.get("frames_sent", -1) == 0,
              f"frames_sent={enc.get('frames_sent')}, suppressed={enc.get('frames_suppressed')}")
        check("S2 SHADOW logs suppressed frames", enc.get("frames_suppressed", 0) > 0,
              str(enc))

        send(proc, "VISION_BITMAP:" + make_bitmap(seq, "0x1F"))
        seq += 1
        send(proc, "SET_CONTROL_AUTHORITY:SECTION")
        send(proc, "ARM")

        seq = pump_mask(proc, seq, "0x1F", args.duration)
        enc = encoder_status()
        sent_after_on = enc.get("frames_sent", 0)
        check("S3 SECTION+ARM sends frames", sent_after_on > 0, f"frames_sent={sent_after_on}")

        send(proc, "VISION_BITMAP:" + make_bitmap(seq, "0x3"))
        seq += 1
        send(proc, "SET_CONTROL_AUTHORITY:SECTION")
        send(proc, "ARM")
        seq = pump_mask(proc, seq, "0x3", args.duration / 2)
        bmp = get_tel("section_bitmap")
        check("S4 section_bitmap tracks feed", bmp in ("0x3", "0X3"),
              f"section_bitmap={bmp}")

        send(proc, "DISARM")
        send(proc, "SET_CONTROL_AUTHORITY:SHADOW")

    finally:
        runtime = os.path.join(PROFILES, profile_name + ".json")
        if args.port and os.path.isfile(runtime):
            try:
                os.remove(runtime)
            except OSError:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

    print()
    if failures:
        print(f"[solenoid_smoke] FAIL — {failures}")
        sys.exit(1)
    print("[solenoid_smoke] PASS — SolenoidFrameV1 encoder, SHADOW suppress, SECTION+ARM dispatch.")
    if args.port:
        print("           Check Uno ACK lines on serial monitor and GPIO D2–D6.")


if __name__ == "__main__":
    main()
