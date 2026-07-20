# PUFworks-actuation — Implementation Plan

**Status:** DRAFT v0.1 — scaffold approved; implementation not started  
**Date:** 2026-06-26  
**Decision:** Option 2 — separate actuation sidecar; vision stays unified in `PUFworks-vision`

**Authoritative inputs:**

- `BOUNDARY.md`, `INTEGRATION_SEAM.md`, `SAFETY.md`
- `PUFworks-contracts/schemas/section_bitmap.v1.json`
- `PUFworks-isobus/bus_engine.py` — ingest + staleness reference
- `PUFworks-vision/bench/pipeline.py` — cross-process bench pattern
- `PUFworks-vision/Plans/SectionOutput/PLAN.md` — operator trigger surface (shared concept)

---

## 1. Goal

Build a **direct actuation gateway** that:

1. Consumes the same `SectionBitmapV1` feed as ISOBUS
2. Applies the same **300 ms staleness fail-safe** and authority ladder
3. Encodes outputs for **custom CAN**, **MCU solenoid banks**, and **0–5 V rate control**
4. Never duplicates vision / GoB logic

ISOBUS field work (Goldacres, 616R) continues in parallel in `PUFworks-isobus`.

---

## 2. Architecture

```
SectionBitmapV1 (vision stdout)
        │
        ▼
┌───────────────────────────────────┐
│  actuation_engine.py              │
│  ├─ ingest_vision_bitmap()        │  ← mirror isobus semantics
│  ├─ _service_watchdogs()          │  ← UI HB, vision stale, speed
│  ├─ _output_allowed()             │  ← OBSERVE…SECTION + ARM
│  ├─ compute_actuator_command()    │  → ActuatorCommandV1 (internal)
│  └─ encoder.dispatch(profile)     │
└───────────────┬───────────────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
 solenoid_mcu  template_can  (future: dac_rate)
 USB serial    socketcan
```

**Threading:** single main loop @ 10–20 Hz; encoder I/O non-blocking; MCU protocol on
a dedicated writer thread with drop-old queue (never block ingest).

---

## 3. Contract work (PUFworks-contracts — before Phase 2 code)

| Schema | Purpose | Priority |
| :-- | :-- | :-- |
| `ActuatorCommandV1` | Normalized post-safety mask + rate + metadata | P0 |
| `ActuatorProfileV1` | Machine config: transport, bit map, scaling | P0 |
| `ActuationTelemetryV1` | stdout telemetry for bench UI | P1 |
| `SolenoidFrameV1` | MCU wire format (USB serial) | P1 |

Append-only ratification in contracts; Python validation copied from vision/isobus pattern.

---

## 4. Phased implementation

### Phase 0 — Scaffold ✅

- [x] README, BOUNDARY, SAFETY, INTEGRATION_SEAM
- [x] Plans/PLAN.md
- [x] Workspace `AGENTS.md` repo map entry
- [ ] GitHub repo created + initial push (manual)

**Exit:** docs review; no runtime code required.

---

### Phase 1 — Ingest + SHADOW gateway ✅

**Deliverables:**

- [x] `actuation_engine.py` — stdin loop, `VISION_BITMAP:` ingest, contract validation
- [x] `_vision_fresh()` — 300 ms staleness, force `0x0`
- [x] Authority ladder stub — OBSERVE / SHADOW / SECTION; `_output_allowed()`
- [x] `UI_HEARTBEAT` watchdog
- [x] stdout `TELEMETRY:` JSON (minimal)
- [x] `bench/actuation_smoke.py` — synthetic bitmap feed, staleness trip
- [x] `requirements.txt` — minimal (no OpenCV, no python-can unless Phase 4)

**Exit criteria:**

- SHADOW mode logs intended mask; **zero bytes** to hardware
- Stale feed → all closed within one tick
- Kill stdin feed → fail-safe ≤ 300 ms

**Reference port:** `PUFworks-isobus/bus_engine.py` `ingest_vision_bitmap`, `_vision_fresh`,
`_service_watchdogs` (CAN RX interlock omitted).

---

### Phase 2 — Vision pipeline bench ✅

**Deliverables:**

- [x] `bench/pipeline.py` — spawn `vision_engine.py` + `actuation_engine.py`, bridge
  `SECTION_BITMAP:` → `VISION_BITMAP:`, forward `UI_HEARTBEAT`
- [x] Kill-vision test (same as isobus pipeline)

**Exit criteria:**

- Vision bitmap tracks actuation SHADOW log ≥90% over 8 s synthetic run (telemetry lag window)
- Kill vision → actuation reports all closed ≤ 450 ms (300 ms contract + tick + telemetry lag)

---

### Phase 3 — Solenoid MCU encoder (first hardware path) ✅

**Deliverables:**

- [x] `encoders/solenoid_mcu.py` — `SolenoidFrameV1` over USB serial
- [x] `profiles/bench_5section.json` — 5-channel bench rig
- [x] `docs/MCU_PROTOCOL.md` — wire protocol + MCU fail-safe rules
- [x] `firmware/src/main.cpp` — Uno reference firmware (SOLENOID parser + 300 ms fail-safe)
- [x] `bench/solenoid_smoke.py` — mock transport + optional COM port
- [x] Contracts: `SolenoidFrameV1`, `ActuatorProfileV1` in `PUFworks-contracts`

**Wire format (draft):**

```
SOLENOID:{"schema":"SolenoidFrameV1","seq":N,"mask":"0x1F","duty":[100,100,0,0,0],"hb":1}
```

MCU rules: no heartbeat > 300 ms → all outputs LOW.

**Exit criteria:**

- SHADOW: frames logged, not sent (or sent to `/dev/null` mock)
- SECTION+ARM: mask changes visible on scope / LED within 50 ms of vision change
- Heartbeat loss → MCU safe state verified

---

### Phase 4 — Custom CAN template encoder

**Deliverables:**

- `encoders/template_can.py` — load frame template from profile JSON
- `profiles/example_custom_can.json` — one sniffed frame from workshop
- Optional `python-can` dependency (profile-gated)
- Virtual bus smoke with `can.interface='virtual'`

**Exit criteria:**

- SHADOW: frames computed + logged, not sent
- Virtual bus: expected ID/payload on section toggle

---

### Phase 5 — Rate / analog path (optional v1.1)

**Deliverables:**

- Boom-wide `rate_l_ha` or `duty_pct` in `ActuatorCommandV1`
- MCU DAC/PWM channel or single 0–5 V output
- Combine: `effective_duty[i] = open[i] ? rate_duty : 0`

**Deferred if v1 binary on/off is sufficient for first field rig.**

---

### Phase 6 — Shell routing (PUFworks-shell)

**Deliverables:**

- `shell.config.json` `downstream: ["isobus"] | ["actuation"] | ["isobus","actuation"]`
- Fan-out `SECTION_BITMAP:` with rename to each consumer stdin
- Single `UI_HEARTBEAT` fan-out

**Not blocking Phases 1–4** — manual pipe OK for workshop.

---

## 5. Profile model (sketch)

```json
{
  "schema": "ActuatorProfileV1",
  "name": "bench_solenoid_5",
  "encoder": "solenoid_mcu",
  "section_count": 5,
  "transport": {
    "type": "usb_serial",
    "port": "COM5",
    "baud": 115200
  },
  "section_map": {
    "vision_lsb_left": true,
    "shift": 0,
    "invert": false
  },
  "rate": {
    "mode": "none"
  }
}
```

Custom CAN profiles add a `frames[]` array with template fields (see brainstorm doc).

---

## 6. Open decisions

| # | Question | Recommendation |
| :-- | :-- | :-- |
| 1 | First hardware target: solenoid MCU vs custom CAN? | **Solenoid MCU** — galvanic isolation, simpler bench |
| 2 | ARM UI in vision app vs shell? | **Shell** for v1; vision stdout commands optional |
| 3 | Shared Python package for ingest logic? | **Copy first**, extract to contracts/helper if duplication hurts |
| 4 | Per-section PWM in v1? | **Binary on/off v1**; PWM duty field optional in frame |
| 5 | Run isobus + actuation simultaneously? | **Workshop: one at a time** until mechanical isolation reviewed |

---

## 7. Success metrics (field-ready actuation path)

1. Same vision binary drives isobus SHADOW and actuation SHADOW with identical bitmap logs
2. Kill-vision fail-safe ≤ 300 ms on actuation bench
3. SECTION+ARM solenoid bench: correct section toggles on GoB replay folder
4. No vision code imports actuation or branches on downstream

---

## 8. Out of scope (explicit)

- Boom MCU firmware implementation (spec only until hardware owner assigned)
- ISOBUS Goldacres bit-shift implementation (stays in isobus)
- GoG / YOLO / agronomy capture
- Public installer / auto-updater
- Wi‑Fi as spray-critical transport

---

*Phase 3 complete. Next: Phase 4 — `encoders/template_can.py` custom CAN profile encoder.*
