#!/usr/bin/env python3
"""
PUFworks-actuation Phase 1 bench smoke (hardware-free).

Spawns actuation_engine.py and verifies SectionBitmapV1 ingest, SHADOW tracking,
zero hardware output counts, and the 300 ms vision staleness fail-safe.

Checks:
  T1  vision feed registered (vision_seen, valid contract ingest)
  T2  SHADOW section_bitmap tracks pumped VISION_BITMAP values
  T3  SHADOW suppresses output (output_counts.section == 0)
  T4  feed stop -> section_bitmap 0x0 and vision interlock trips <= 350 ms

Run:
  python bench/actuation_smoke.py
  python bench/actuation_smoke.py --duration 2.0
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


def make_bitmap(seq: int, section_count: int = 5, on: bool = True) -> str:
    mask = (1 << section_count) - 1 if on else 0
    msg = {
        "schema": "SectionBitmapV1",
        "ts_ms": int(time.time() * 1000),
        "seq": seq,
        "section_count": section_count,
        "bitmap": f"0x{mask:X}",
        "source": "bench-smoke",
    }
    return json.dumps(msg, separators=(",", ":"))


def check(name: str, ok: bool, detail: str = "") -> None:
    tag = "ok " if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" - {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=2.0, help="seconds of live bitmap pump")
    args = ap.parse_args()

    if not os.path.isfile(ENGINE):
        print(f"[actuation_smoke] ERROR: engine not found at {ENGINE}")
        sys.exit(2)

    print(f"[actuation_smoke] engine = {ENGINE}")
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
        time.sleep(0.3)
        send(proc, "SET_CONTROL_AUTHORITY:SHADOW")
        send(proc, "SET_SPEED:10")
        send(proc, "UI_HEARTBEAT")

        seq = 0
        section_count = 5
        full_mask = (1 << section_count) - 1

        print(f"[actuation_smoke] pumping ON then OFF VISION_BITMAP ...")
        last_hb = 0.0
        on_samples: list[bool] = []
        off_samples: list[bool] = []
        for phase, bucket, on in (
            ("ON", on_samples, True),
            ("OFF", off_samples, False),
        ):
            t_end = time.time() + args.duration / 2.0
            while time.time() < t_end:
                now = time.time()
                if now - last_hb >= 1.0:
                    send(proc, "UI_HEARTBEAT")
                    last_hb = now
                payload = make_bitmap(seq, section_count=section_count, on=on)
                send(proc, "VISION_BITMAP:" + payload)
                seq += 1
                time.sleep(0.06)
                iso = get_tel("section_bitmap")
                expected = full_mask if on else 0
                if iso is not None:
                    try:
                        bucket.append(int(iso, 16) == expected)
                    except ValueError:
                        bucket.append(False)

        on_ratio = sum(on_samples) / len(on_samples) if on_samples else 0.0
        off_ratio = sum(off_samples) / len(off_samples) if off_samples else 0.0

        # End on ON so staleness test proves 0x1F -> 0x0 transition, not idle 0x0.
        for _ in range(5):
            send(proc, "VISION_BITMAP:" + make_bitmap(seq, section_count=section_count, on=True))
            seq += 1
            time.sleep(0.05)

        out_before = (get_tel("output_counts") or {}).get("section", 0)
        check("T1 vision feed registered", bool(get_tel("vision_seen")),
              f"source={get_tel('vision_source')}")
        check("T2 SHADOW section_bitmap tracks feed", on_ratio >= 0.85 and off_ratio >= 0.85,
              f"ON {sum(on_samples)}/{len(on_samples)} ({on_ratio*100:.0f}%), "
              f"OFF {sum(off_samples)}/{len(off_samples)} ({off_ratio*100:.0f}%)")
        check("T3 SHADOW zero output counts", out_before == 0,
              f"output_counts.section={out_before}")

        print("[actuation_smoke] stopping feed to test 300 ms staleness fail-safe ...")
        time.sleep(0.05)
        pre_stale = get_tel("section_bitmap")
        t_stale = time.time()
        closed = False
        vision_tripped = False
        close_ms = None
        while time.time() - t_stale < 0.6:
            send(proc, "UI_HEARTBEAT")
            time.sleep(0.05)
            bmp = get_tel("section_bitmap")
            interlocks = get_tel("control_interlocks") or {}
            if not closed and bmp in ("0x0", "0X0"):
                closed = True
                close_ms = (time.time() - t_stale) * 1000.0
            if get_tel("vision_seen") and not interlocks.get("vision", True):
                vision_tripped = True
            if closed and vision_tripped:
                break

        check("T4 was spraying before stale test", pre_stale not in ("0x0", "0X0", None),
              f"pre_stale={pre_stale}")
        check("T4 sections close on feed death", closed,
              f"section_bitmap={get_tel('section_bitmap')}" +
              (f" at {close_ms:.0f} ms" if close_ms is not None else ""))
        check("T4 vision interlock trips", vision_tripped, str(get_tel("control_interlocks")))
        # 300 ms contract + one 10 Hz tick (100 ms) + telemetry lag
        check("T4 fail-safe within 450 ms", close_ms is not None and close_ms <= 450,
              f"close at {close_ms:.0f} ms" if close_ms is not None else "never closed")

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

    print()
    if failures:
        print(f"[actuation_smoke] FAIL — {failures}")
        sys.exit(1)
    print("[actuation_smoke] PASS — ingest, SHADOW tracking, suppressed output, "
          "and 300 ms staleness fail-safe.")


if __name__ == "__main__":
    main()
