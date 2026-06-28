# Integration Seam — Vision → Actuation (and ISOBUS)

**One-page contract.** Agents and integrators: read this before cross-repo changes.

---

## Principle

**Vision decides. Downstream encodes. Shell routes (later).**

```
┌─────────────────────┐
│  PUFworks-vision    │  Camera → GoB → whole-section zones → hysteresis
│  vision_engine.py   │  Publishes SectionBitmapV1 @ 10–20 Hz (even 0x0)
└──────────┬──────────┘
           │ stdout: SECTION_BITMAP:{json}
           ▼
┌─────────────────────┐     optional later
│  PUFworks-shell     │──── downstream: isobus | actuation | both
│  (integrator)       │
└──────────┬──────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
┌─────────┐ ┌──────────────┐
│ isobus  │ │ actuation    │   Parallel consumers — same feed, different encoders
│ engine  │ │ engine       │
└────┬────┘ └──────┬───────┘
     ▼             ▼
  ISOBUS CAN    MCU / custom CAN / PWM
```

---

## Upstream: what vision MUST do

| Rule | Detail |
| :-- | :-- |
| **Single publisher** | One detection core; no `if actuation` branches in vision |
| **Fixed rate** | 10–20 Hz `SECTION_BITMAP:` even when bitmap is `0x0` |
| **Schema** | `SectionBitmapV1` from `PUFworks-contracts` |
| **Bit order** | LSB = section 1 = leftmost boom section |
| **Silence = death** | Never stop publishing; `0x0` means "all closed", not "idle" |
| **No actuation** | Vision never drives CAN, serial solenoids, or ARM state locally |

Vision may emit operator **commands** on stdout (`UI_HEARTBEAT`, `SET_CONTROL_AUTHORITY`,
`ARM`) for an integrator to forward — but authority is **enforced downstream**, not in vision.

---

## Seam: wire format (today → actuation)

Copied from the proven shell → isobus bridge:

| Direction | Line | Notes |
| :-- | :-- | :-- |
| vision → consumer | `SECTION_BITMAP:{json}` | Raw vision stdout |
| integrator rename | `VISION_BITMAP:{json}` | Same JSON; prefix distinguishes ingest |
| UI host → consumer | `UI_HEARTBEAT` | 1 Hz mandatory while armed |
| UI host → consumer | `SET_CONTROL_AUTHORITY:{RUNG}` | OBSERVE … SECTION |
| UI host → consumer | `ARM` / `DISARM` | Actuation gate |

Consumers validate JSON against `section_bitmap.v1.json` and stamp `vision_last_rx`.

---

## Downstream: shared ingest semantics (actuation = isobus)

Both `PUFworks-isobus` and `PUFworks-actuation` MUST implement identical feed handling:

| Event | Behaviour |
| :-- | :-- |
| First valid message | Latch `section_count`; mark feed seen |
| Fresh message (< 300 ms) | Use `bitmap` as section intent |
| Stale (> 300 ms) | Force all sections **CLOSED**; never hold last value |
| Armed + stale | Demote to SHADOW, disarm (actuation ladder) |
| Invalid JSON | Reject line; do not update bitmap |

---

## Downstream: where paths DIVERGE

| Aspect | `PUFworks-isobus` | `PUFworks-actuation` |
| :-- | :-- | :-- |
| Goldacres DDI 141 | Yes (`0xCC`, bit shift) | No |
| 616R GreenSeeker serial | Yes (whole-boom blank) | No |
| Custom CAN template | No | Yes (profile JSON) |
| MCU solenoid PWM | No | Yes (USB/UDP protocol) |
| 0–5 V rate DAC | No (profile suppressed on GRC) | Yes (via MCU) |
| J1939 address claim | Yes | No |

Same `SectionBitmapV1` in; different encoder out.

---

## Normalized layer (planned)

Before hardware encoding, the actuation gateway will compute **`ActuatorCommandV1`**:

- Post-interlock section mask
- Optional `rate_l_ha` / per-section duty (v2)
- `authority`, `armed`, `interlocks_ok`
- `source_seq` tying back to vision

Profiles map `ActuatorCommandV1` → bytes/GPIO. Schema lives in `PUFworks-contracts` when ratified.

---

## Bench parity (prevent vision divergence)

Run the same vision process against each consumer:

```powershell
# ISOBUS (exists today)
python PUFworks-vision\bench\pipeline.py --duration 8

# Actuation (planned)
python PUFworks-actuation\bench\pipeline.py --duration 8
```

Both must pass: bitmap tracks vision in SHADOW; kill vision → all sections close ≤ 300 ms.

---

## Shell routing (deferred — config sketch)

Future `shell.config.json`:

```json
{
  "downstream": ["isobus"],
  "vision_engine": "../PUFworks-vision/vision_engine.py",
  "isobus_engine": "../PUFworks-isobus/bus_engine.py",
  "actuation_engine": "../PUFworks-actuation/actuation_engine.py"
}
```

`downstream: ["isobus", "actuation"]` fans out each `SECTION_BITMAP:` line to both stdin
feeds (with rename). Vision binary unchanged.

---

## Agent rules (all repos)

1. **Never import across actuation ↔ vision** — stdin/stdout + contracts only.
2. **Improve detection in vision only** — both paths pick it up on next run.
3. **New hardware = new profile + encoder** — not a vision fork.
4. **Ratify schemas in contracts** before code depends on them.
5. **Read `PUFworks-isobus/SAFETY.md`** for ladder semantics; actuation mirrors them.
