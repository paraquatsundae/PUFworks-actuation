# Persistent Agent Instructions — PUFworks-actuation

Direct actuation gateway for custom CAN, MCU solenoids, and analog rate control.
**Vision stays in `PUFworks-vision`** — this repo only encodes `SectionBitmapV1`.

## Read first

| Doc | When |
| :-- | :-- |
| `BOUNDARY.md` | Any new feature — confirms ownership |
| `SAFETY.md` | Any output path or interlock change |
| `INTEGRATION_SEAM.md` | Cross-repo / vision / shell work |
| `Plans/PLAN.md` | Implementation order and phases |

For ISOBUS-specific CAN (Goldacres, 616R), use `PUFworks-isobus` instead.

## Key rules

1. **No vision fork** — never add GoB, camera, or zone logic here. Ingest `SectionBitmapV1` only.
2. **Staleness = 300 ms** — match `PUFworks-isobus` ingest semantics exactly.
3. **Boot OBSERVE** — zero hardware output until SECTION + ARM.
4. **MCU drives coils** — laptop sends commands; MCU owns fail-safe GPIO.
5. **New hardware = new profile + encoder** — no magic bytes in the engine.
6. **Schemas in contracts** — ratify in `PUFworks-contracts` before depending on them.
7. **Do not commit** unless the user asks. Git on Windows: `"C:\Program Files\Git\cmd\git.exe"`.

## Planned entry points

| Area | Path |
| :-- | :-- |
| Gateway engine | `actuation_engine.py` (Phase 1) |
| Encoders | `encoders/solenoid_mcu.py` |
| MCU protocol | `docs/MCU_PROTOCOL.md` |
| Firmware | `firmware/src/main.cpp` |
| Profiles | `profiles/bench_5section.json` |
| Bench | `bench/actuation_smoke.py`, `bench/pipeline.py`, `bench/solenoid_smoke.py` |

## Wire protocol (mirror isobus ingest)

- **In:** `VISION_BITMAP:{SectionBitmapV1 json}`, `UI_HEARTBEAT`, `SET_CONTROL_AUTHORITY:`, `ARM`/`DISARM`
- **Out:** `TELEMETRY:{ActuationTelemetryV1 json}`, `[ACTUATION_LOG]`

Vision publishes `SECTION_BITMAP:` — integrator renames to `VISION_BITMAP:` (same as shell → isobus).

## Cross-repo layout

```
C:\Projects\
  PUFworks-contracts\
  PUFworks-vision\      ← detection (improve here; both paths benefit)
  PUFworks-isobus\      ← ISOBUS path (parallel)
  PUFworks-actuation\   ← this repo
  PUFworks-shell\       ← routing later
```

## Bench (once Phase 1 exists)

```powershell
python bench/actuation_smoke.py --duration 2
python bench/pipeline.py --duration 8   # Phase 2 — vision + actuation (P1–P4)
python bench/solenoid_smoke.py          # Phase 3 — solenoid encoder (mock)
```

Reference: `PUFworks-vision/bench/pipeline.py` for spawn/bridge pattern.
