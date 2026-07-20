# MCU serial protocol — SolenoidFrameV1

Authoritative wire contract between `actuation_engine.py` (gateway) and boom MCU firmware.
Schema: `PUFworks-contracts/schemas/solenoid_frame.v1.json`

---

## Transport

| Parameter | Value |
| :-- | :-- |
| Physical | USB serial (CDC) |
| Baud | 115200 (profile default) |
| Framing | One ASCII line per frame, `\n` terminated |
| Direction | Gateway → MCU (commands); MCU → Gateway (optional ACK) |

---

## Gateway → MCU: `SOLENOID:` line

```
SOLENOID:{"schema":"SolenoidFrameV1","seq":N,"mask":"0x1F","duty":[100,100,0,0,0],"hb":1}
```

| Field | Type | Notes |
| :-- | :-- | :-- |
| `schema` | const | `SolenoidFrameV1` |
| `seq` | int ≥ 0 | Monotonic gateway sequence (incremented on each **armed** dispatch) |
| `mask` | hex string | Section bitmask after safety + `section_map`. **LSB = section 1** |
| `duty` | int[] | Per-section duty 0–100. v1 binary path: 100 = open, 0 = closed |
| `hb` | 0 \| 1 | Heartbeat flag. MCU treats `hb=1` as gateway liveness |

### Dispatch rules (gateway)

- **SHADOW / DISARM / OBSERVE:** frame is built and logged; **not sent** on serial.
- **SECTION + ARM:** frame sent at engine tick rate (~10 Hz) with `hb=1`.
- Mask source: `section_bitmap` after interlocks (speed, UI, vision staleness).
- Writer thread uses drop-old queue — never blocks ingest.

---

## MCU → Gateway: optional `ACK:` line

Bench firmware may echo:

```
ACK:{"seq":N,"mask":"0x1F","safe":0}
```

| Field | Meaning |
| :-- | :-- |
| `seq` | Echo of last applied command sequence |
| `mask` | Echo of applied mask |
| `safe` | `1` if MCU is in fail-safe (all outputs OFF) |

Gateway may ignore ACK in v1; useful for scope/serial monitor bring-up.

---

## MCU fail-safe (non-negotiable)

From `SAFETY.md`:

1. **No valid frame with `hb=1` for > 300 ms** → force all solenoid outputs **OFF** (LOW).
2. **Boot** → all outputs OFF until first valid armed frame received.
3. **Parse error** → ignore line; do not change outputs unless staleness timer fires.
4. **Coil drivers on MCU only** — laptop/gateway never switches high current.

The 300 ms window matches `SOLENOID_FRAME_STALE_MS` in contracts and vision
`SECTION_BITMAP_STALE_MS`.

---

## Bench rig pin map (Arduino Uno hello firmware)

Profile `bench_5section.json`:

| Section | GPIO | Notes |
| :-- | :-- | :-- |
| 1 | D2 | LSB |
| 2 | D3 | |
| 3 | D4 | |
| 4 | D5 | |
| 5 | D6 | |
| — | D13 | Onboard LED mirrors “any section open” |

Production boom MCU will use MOSFET drivers with flyback protection on a separate
12 V rail — not implemented in the throwaway Uno scaffold.

---

## Related files

| Path | Role |
| :-- | :-- |
| `encoders/solenoid_mcu.py` | Gateway encoder |
| `profiles/bench_5section.json` | Workshop profile (COM port) |
| `profiles/bench_5section_mock.json` | Headless bench (no serial) |
| `firmware/src/main.cpp` | Uno reference firmware |
| `bench/solenoid_smoke.py` | SHADOW + ARM smoke tests |
