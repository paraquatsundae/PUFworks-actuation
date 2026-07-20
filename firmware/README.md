# PUFworks-actuation — MCU firmware (Phase 3)

Reference firmware for the 5-section bench rig. Parses `SolenoidFrameV1` lines from
`actuation_engine.py` and drives GPIO outputs with a 300 ms fail-safe.

**Board:** Arduino Uno (`platformio.ini` → `[env:uno]`)

See `docs/MCU_PROTOCOL.md` for the full wire contract.

## Pin map

| Section | GPIO |
| :-- | :-- |
| 1 (LSB) | D2 |
| 2 | D3 |
| 3 | D4 |
| 4 | D5 |
| 5 | D6 |
| Activity | D13 (onboard LED — ON when any section open) |

## Build & upload

```powershell
cd C:\Projects\PUFworks-actuation\firmware
pio run -t upload
pio device monitor
```

Set `upload_port` / `monitor_port` in `platformio.ini` when needed.

## Expected behaviour

1. Boot: `PUFworks-actuation MCU ready (SolenoidFrameV1)`
2. Valid `SOLENOID:{...}` with `"hb":1` → drives D2–D6, replies `ACK:{...}`
3. No valid frame for > 300 ms → all outputs LOW, `ACK:...,"safe":1`
4. `HELLO` / `PING` → `PONG` (serial sanity check)

## Gateway bench (mock — no hardware)

```powershell
cd C:\Projects\PUFworks-actuation
python bench/solenoid_smoke.py
```

## Gateway bench (Uno on COMx)

Update `profiles/bench_5section.json` port, or:

```powershell
python bench/solenoid_smoke.py --profile bench_5section --port COM6
```

Then SECTION+ARM from smoke (or manual pipe) should toggle D2–D6 within one tick.
