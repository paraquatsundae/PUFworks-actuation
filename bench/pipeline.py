#!/usr/bin/env python3
"""
PUFworks-actuation Phase 2 exit-criteria demo: GoB -> SectionBitmapV1 -> actuation SHADOW.

Wires two independent processes over the locked SectionBitmapV1 contract:

  vision_engine.py (--synthetic)              actuation_engine.py
      |  SECTION_BITMAP:{...}  --[bridge]-->  VISION_BITMAP:{...}
      |                                          |
      |                                          v  (SHADOW: compute + log, zero hardware TX)
      +---- this script reads both and asserts actuation section_bitmap == vision feed

Verifies:
  P1  actuation registers the live vision feed (vision_seen, source = vision-gob)
  P2  actuation section_bitmap in SHADOW tracks the vision-published bitmap
  P3  SHADOW suppresses output (output_counts.section == 0)
  P4  killing the vision process trips the 300 ms staleness fail-safe across the
      process boundary: sections close (0x0) and the vision interlock drops

Run:
  python bench/pipeline.py --duration 8
  python bench/pipeline.py --vision C:\\Projects\\PUFworks-vision\\vision_engine.py
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
ACTUATION = os.path.normpath(os.path.join(HERE, "..", "actuation_engine.py"))
DEFAULT_VISION = os.path.normpath(os.path.join(HERE, "..", "..", "PUFworks-vision", "vision_engine.py"))

telemetry: dict = {}
tel_lock = threading.Lock()
last_vision = {"bitmap": "0x0", "seq": -1, "ts": 0.0}
vision_history: list[tuple[float, int]] = []
vlock = threading.Lock()
match_samples: list[bool] = []
failures: list[str] = []

# Telemetry lags the live vision feed by up to ~1 tick + ingest. Match the actuation
# bitmap against any vision value published within this window, not just the newest one.
MATCH_WINDOW_S = 0.35


def actuation_reader(proc: subprocess.Popen) -> None:
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if line.startswith("TELEMETRY:"):
            try:
                t = json.loads(line[len("TELEMETRY:"):])
                with tel_lock:
                    telemetry.clear()
                    telemetry.update(t)
            except json.JSONDecodeError:
                pass
        elif line.startswith("[ACTUATION_LOG]"):
            print(f"  act> {line}")


def vision_reader(vproc: subprocess.Popen, aproc: subprocess.Popen) -> None:
    """Forward every SECTION_BITMAP from vision into actuation as VISION_BITMAP."""
    for raw in vproc.stdout:
        line = raw.rstrip("\n")
        if line.startswith("SECTION_BITMAP:"):
            payload = line[len("SECTION_BITMAP:"):]
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                continue
            now = time.time()
            with vlock:
                last_vision["bitmap"] = msg.get("bitmap", "0x0")
                last_vision["seq"] = msg.get("seq", -1)
                last_vision["ts"] = now
                try:
                    vision_history.append((now, int(msg.get("bitmap", "0x0"), 16)))
                except ValueError:
                    pass
                cutoff = now - 2.0
                while vision_history and vision_history[0][0] < cutoff:
                    vision_history.pop(0)
            try:
                aproc.stdin.write("VISION_BITMAP:" + payload + "\n")
                aproc.stdin.flush()
            except (BrokenPipeError, ValueError):
                return
        elif line.startswith("[VISION_LOG]"):
            print(f"  vis> {line}")


def get_tel(key, default=None):
    with tel_lock:
        return telemetry.get(key, default)


def send(proc: subprocess.Popen, line: str) -> None:
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=8.0, help="live-feed seconds before staleness test")
    ap.add_argument("--vision", default=DEFAULT_VISION, help="path to PUFworks-vision/vision_engine.py")
    args = ap.parse_args()

    if not os.path.isfile(args.vision):
        print(f"[pipeline] ERROR: vision engine not found at {args.vision}")
        print("           Pass --vision <path to PUFworks-vision/vision_engine.py>.")
        sys.exit(2)
    if not os.path.isfile(ACTUATION):
        print(f"[pipeline] ERROR: actuation engine not found at {ACTUATION}")
        sys.exit(2)

    print(f"[pipeline] vision    = {args.vision}")
    print(f"[pipeline] actuation = {ACTUATION}")

    aproc = subprocess.Popen(
        [sys.executable, "-u", ACTUATION],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=os.path.dirname(ACTUATION),
    )
    vproc = subprocess.Popen(
        [sys.executable, "-u", args.vision, "--synthetic", "--no-preview"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=os.path.dirname(args.vision),
    )

    threading.Thread(target=actuation_reader, args=(aproc,), daemon=True).start()
    threading.Thread(target=vision_reader, args=(vproc, aproc), daemon=True).start()

    try:
        time.sleep(1.0)
        send(aproc, "SET_CONTROL_AUTHORITY:SHADOW")
        send(aproc, "SET_SPEED:10")
        send(vproc, "SET_MODE:GoB")
        send(vproc, "START_CAMERA")

        print(f"[pipeline] streaming live GoB feed for {args.duration:.0f}s ...")
        out_before = (get_tel("output_counts") or {}).get("section", 0)
        t0 = time.time()
        last_hb = 0.0
        while time.time() - t0 < args.duration:
            now = time.time()
            if now - last_hb >= 1.0:
                send(aproc, "UI_HEARTBEAT")
                last_hb = now
            with vlock:
                feed_live = last_vision["seq"] >= 0 and (now - last_vision["ts"]) < 0.20
                recent = {b for ts, b in vision_history if ts >= now - MATCH_WINDOW_S}
            act_bits_s = get_tel("section_bitmap")
            if act_bits_s is not None and feed_live and recent:
                try:
                    match_samples.append(int(act_bits_s, 16) in recent)
                except ValueError:
                    match_samples.append(False)
            time.sleep(0.05)

        print("[pipeline] live-feed checks")
        check("P1 actuation registered live vision feed", bool(get_tel("vision_seen")),
              f"source={get_tel('vision_source')}")
        matches = sum(1 for m in match_samples if m)
        ratio = matches / len(match_samples) if match_samples else 0.0
        check("P2 SHADOW section_bitmap tracks vision", ratio >= 0.90,
              f"{matches}/{len(match_samples)} samples matched ({ratio * 100:.0f}%)")
        out_after = (get_tel("output_counts") or {}).get("section", 0)
        check("P3 SHADOW holds, zero hardware output", out_after == out_before == 0,
              f"authority={get_tel('control_authority')}, output_counts.section "
              f"{out_before}->{out_after}")

        print("[pipeline] killing vision process to test 300 ms staleness fail-safe ...")
        vproc.terminate()
        try:
            vproc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            vproc.kill()
        t_stale = time.time()
        closed = False
        vision_tripped = False
        close_ms = None
        while time.time() - t_stale < 1.5:
            send(aproc, "UI_HEARTBEAT")
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

        check("P4 sections close on vision death", closed,
              f"section_bitmap={get_tel('section_bitmap')}" +
              (f" at {close_ms:.0f} ms" if close_ms is not None else ""))
        check("P4 vision interlock trips on vision death", vision_tripped,
              str(get_tel("control_interlocks")))
        check("P4 fail-safe within 450 ms", close_ms is not None and close_ms <= 450,
              f"close at {close_ms:.0f} ms" if close_ms is not None else "never closed")

    finally:
        for p in (vproc, aproc):
            try:
                p.terminate()
            except Exception:
                pass

    print()
    if failures:
        print(f"[pipeline] FAIL ({len(failures)}): {failures}")
        sys.exit(1)
    print("[pipeline] PASS — GoB vision drives actuation SHADOW over SectionBitmapV1, "
          "and publisher death fails safe. No camera, no hardware.")
    sys.exit(0)


if __name__ == "__main__":
    main()
