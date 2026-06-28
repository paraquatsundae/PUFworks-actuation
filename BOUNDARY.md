# PUFworks-actuation — Repository Boundary

**Status:** Approved v0.1 (scaffold)  
**Date:** 2026-06-26  
**Parent:** `PUFVision/BOUNDARY.md` Option 2 — direct actuation sidecar

---

## 1. Role

`PUFworks-actuation` is the **direct hardware actuation gateway** for sprayers
that do **not** use the ISOBUS path in `PUFworks-isobus` (or use it in parallel
for logging only).

It consumes the same **`SectionBitmapV1`** decision feed as the ISOBUS engine
and encodes it for:

- Custom CAN controllers (profile-driven frame templates)
- Boom MCU over USB serial / isolated UDP
- Per-section or boom-wide PWM / 0–5 V rate outputs (via MCU, never laptop GPIO)

It does **not** perform machine vision, ISOBUS address management, or
GreenSeeker serial (616R).

---

## 2. Repository map (actuation in the split)

```
PUFworks-contracts/     Schemas: SectionBitmapV1, ActuatorCommandV1 (planned), profiles
PUFworks-vision/        Camera → GoB → SectionBitmapV1 (ONE detection core for ALL paths)
PUFworks-isobus/        ISOBUS / JD / Goldacres / GreenSeeker serial
PUFworks-actuation/     Custom CAN + MCU solenoid / analog rate (THIS REPO)
PUFworks-shell/         Optional integrator; routes vision stdout to 1..N consumers
PUFworks-agronomy/      Offline datasets only — never in actuation loop
```

---

## 3. What this repo OWNS

| Asset | Responsibility |
| :-- | :-- |
| `actuation_engine.py` | Gateway process: ingest, safety ladder, encoder dispatch |
| `encoders/` | Profile plugins: `solenoid_mcu`, `template_can`, … |
| `profiles/` | Per-machine `ActuatorProfileV1` JSON (bit order, transport, scaling) |
| `SAFETY.md` | Actuation-specific fail-safe rules |
| `bench/` | Headless smoke, vision→actuation pipeline, kill-feed fail-safe |
| `INTEGRATION_SEAM.md` | Downstream contract with vision + shell |

---

## 4. What this repo MUST NOT own

| Forbidden | Belongs in |
| :-- | :-- |
| Camera capture, GoB, HSV, zones, hysteresis | `PUFworks-vision` |
| ISOBUS address claim, WSM, VT, DDI 141 to GRC `0xCC` | `PUFworks-isobus` |
| GreenSeeker RT200 serial (616R Pathway G/E) | `PUFworks-isobus` |
| JSON Schema source of truth | `PUFworks-contracts` |
| Dataset capture, GoG labeling | `PUFworks-agronomy` |
| Electron cab UI (unless a minimal bench panel is added later) | `PUFworks-shell` |
| Direct MOSFET / solenoid drive from the laptop | **Boom MCU firmware** (out of scope here until specified) |

---

## 5. Vision coupling rule (no fork)

**Vision improvements MUST NOT fork between ISOBUS and actuation.**

- `PUFworks-vision` publishes **one** `SectionBitmapV1` stream regardless of downstream.
- Detection, tuning UI, camera hardening, and zone logic live **only** in vision.
- This repo validates and encodes; it never re-implements greenness or section decisions.
- Bench proof: same `vision_engine.py` output feeds both `bench/pipeline.py` (isobus) and
  `bench/pipeline.py` (actuation) with identical bitmap sequences.

---

## 6. Routing (deferred)

The integrator (`PUFworks-shell`) will eventually choose:

```yaml
downstream: isobus | actuation | both
```

Until then, workshop bring-up uses **manual pipe** or a one-consumer bench harness.
Vision code does not branch on downstream target.

---

## 7. Contract dependencies

| Schema | Status | Used for |
| :-- | :-- | :-- |
| `SectionBitmapV1` | **Exists** in contracts | Upstream feed from vision |
| `ActuatorCommandV1` | **Planned** | Normalized post-safety actuation state |
| `ActuatorProfileV1` | **Planned** | Machine encoder config |
| `ActuationTelemetryV1` | **Planned** | stdout telemetry mirror |

Ratify new schemas in `PUFworks-contracts` before the engine depends on them.

---

## 8. Extraction order (within actuation repo)

1. **Ingest + staleness** — copy the proven `ingest_vision_bitmap` semantics from isobus.
2. **SHADOW mode** — compute + log, zero hardware output.
3. **Solenoid MCU encoder** — USB serial bench (LED click / scope).
4. **Custom CAN template encoder** — one sniffed profile.
5. **Shell routing** — optional fan-out in `PUFworks-shell`.

ISOBUS Goldacres field proof on Clare Downs equipment is **not blocked** by actuation
scaffold work; paths are parallel.

---

## 9. Change control

- Read `SAFETY.md` before any output-path change.
- New encoder = new profile JSON + bench smoke; no ad-hoc bytes in the engine.
- Do not weaken the 300 ms vision staleness fail-safe to match slower MCUs — fix MCU
  or reduce upstream latency instead.
