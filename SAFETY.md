# SAFETY.md — PUFworks-actuation

These rules are load-bearing. Do not bypass them, and do not merge changes that
weaken them without an explicit field-owner decision recorded in `Plans/PLAN.md`.

Adapted from `PUFworks-isobus/SAFETY.md` for direct hardware paths (MCU, custom CAN).

---

## Control Authority ladder

The gateway **boots in `OBSERVE`** — zero hardware output.

| Rung | Output allowed |
| :-- | :-- |
| `OBSERVE` | Nothing — ingest + log only |
| `ANNOUNCE` | Reserved (no bus claim in this repo) |
| `SHADOW` | Compute normalized command + log; **TX suppressed** |
| `SECTION` / `FULL` | Section outputs + optional rate, **only while `ARM`ed** |

Every output path calls `_output_allowed(kind)` (planned). New paths MUST use the same gate.

---

## Interlocks (force-safe, evaluated every 100 ms)

- **Speed** < 0.5 km/h → all sections closed (no demote — normal stationary)
- **UI heartbeat** > 3 s while armed → all sections closed, demote to `SHADOW`, disarm
- **Vision staleness** > 300 ms (once feed seen) → all sections **CLOSED**; while armed,
  demote to `SHADOW`. Never hold last bitmap. Silence = publisher dead, never "no targets".

Actuation does **not** use CAN RX interlocks (no bus claim). If a profile needs bus feedback,
model it as an optional profile-specific health input — it must not disable the vision
staleness rule.

---

## MCU / solenoid rules (non-negotiable)

- **Laptop never drives coils directly.** All high-current switching on the boom MCU.
- **MCU fail-safe:** if gateway heartbeat stops (> 300 ms), MCU forces all outputs OFF.
- **Separate supplies:** 12 V solenoid rail ≠ 5 V logic; flyback protection on every channel.
- **PWM / DAC:** fixed frequency on MCU; gateway sends target duty or voltage, not bit-bang timing.

---

## Vision feed rules

- Publisher MUST emit at 10–20 Hz even at `0x0` (`SectionBitmapV1` contract).
- Subscriber closes all sections on staleness — same semantics as `PUFworks-isobus`.
- Vision can only **reduce** spray (OFF bits, or `0x0`); it cannot bypass authority or ARM.

---

## Platform scope (this repo)

**In scope:** custom sprayers, bench rigs, solenoid banks, profile-driven custom CAN.

**Out of scope (use `PUFworks-isobus`):**

- Goldacres GRC DDI 141 @ `0xCC`
- JD 616R GreenSeeker serial + whole-boom blanking
- Any sanctioned ISOBUS section injection on 616R

Running **both** isobus and actuation against the same boom requires explicit mechanical
isolation review — default workshop config uses **one** actuation path at a time.

---

## Bench vs field

- **SHADOW** is mandatory first rung for any new encoder or profile.
- **ARM** on real hardware requires signed-off profile + `bench/pipeline.py` pass.
- Record sessions under `recordings/` (gitignored); never commit field CSVs.
