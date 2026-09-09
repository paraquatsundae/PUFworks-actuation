# PUFworks-actuation

```
████  █   █ █████ █   █  ███  ████  █   █  ████   
█░░░█ █░  █░█░░░░░█░  █░█ ░░█ █░░░█ █░ █ ░█ ░░░░  
████░░█░░ █░████░░█░█ █░█░ ░█░████░░███ ░ ░███░░░ 
█░░░░ █░░ █░█░░░░ ██░██░█░░ █░█░░█░ █░░█ ░  ░░█   
█░░░░░ ███ ░█░░░░░█░░ █░░███ ░█░░░█░█░░░█ ████░░  
 ░░     ░░░ ░░░    ░░░ ░░ ░░░ ░░░  ░ ░░  ░ ░░░░ ░ 
  ░      ░░░  ░     ░   ░  ░░░  ░   ░ ░   ░ ░░░░  
```

Direct actuation sidecar for the PUFworks sprayer stack. Consumes the same
`SectionBitmapV1` feed as `PUFworks-isobus`, but drives **non-ISOBUS** outputs:
custom CAN profiles, MCU solenoid banks, and 0–5 V rate controllers.

Uses a similar authority-ladder idea for non-ISOBUS gear. **Not an ISOBUS ECU
and not a John Deere product.**

Vision stays unified in `PUFworks-vision`. This repo owns only the **last mile**
from normalized actuation intent to hardware.

```
PUFworks-vision ──SectionBitmapV1──► PUFworks-actuation ──► MCU / custom CAN / PWM
       │                                    ▲
       │                                    │ UI_HEARTBEAT, authority (via integrator)
       └── (same feed, optional) ──► PUFworks-isobus ──► ISO 11783 / J1939 CAN
              (experimental, machine-specific — not AEF-certified)
```

Read `BOUNDARY.md`, `SAFETY.md`, and `INTEGRATION_SEAM.md` before writing code.

## Status

**Phase 2** — `actuation_engine.py` SHADOW gateway + vision pipeline bench. See
`Plans/PLAN.md` for phased bring-up (Phase 3 = solenoid MCU next).

## Why a separate repo

| Concern | `PUFworks-isobus` | `PUFworks-actuation` |
| :-- | :-- | :-- |
| Bus role | ISO 11783 / J1939 on CAN (listen-first; machine-specific) | Profile-driven custom CAN, USB serial to boom MCU |
| Fail-safe model | Control Authority ladder + CAN interlocks | Gateway heartbeat + MCU hardware fail-safe |
| Bench hardware | Virtual CAN, GRC display | Scope on 5 V coils, LED/solenoid click tests |
| Vision coupling | Same `SectionBitmapV1` ingest | Same `SectionBitmapV1` ingest |

Improvements to GoB detection, zones, and camera hardening happen **only** in
`PUFworks-vision` and benefit both downstream paths automatically.

## Prerequisites

Sibling repos (default layout):

```
C:\Projects\
  PUFworks-contracts\     # SectionBitmapV1 schema (+ future ActuatorCommandV1)
  PUFworks-vision\        # vision_engine.py — sole detection publisher
  PUFworks-isobus\        # ISOBUS path (parallel, not a dependency)
  PUFworks-actuation\     # this repo
  PUFworks-shell\         # optional integrator (routing deferred)
```

## Planned layout

```
actuation_engine.py       # stdin/stdout gateway (planned)
profiles/                 # ActuatorProfileV1 JSON per machine (planned)
encoders/                 # solenoid_mcu, template_can, … (planned)
bench/                    # headless smoke + vision pipeline (planned)
Plans/PLAN.md             # phased implementation plan
BOUNDARY.md               # what this repo owns / must not own
INTEGRATION_SEAM.md       # vision ↔ actuation ↔ shell contract
SAFETY.md                 # fail-safe rules (load-bearing)
```

## Wire protocol (planned — mirrors isobus ingest)

**stdin**

| Prefix | Payload |
| :-- | :-- |
| `VISION_BITMAP:` | `SectionBitmapV1` JSON (same rename as shell → isobus bridge) |
| `UI_HEARTBEAT` | Required @ 1 Hz from UI host while armed |
| `SET_CONTROL_AUTHORITY:` | `OBSERVE` … `SECTION` (same ladder semantics) |
| `ARM` / `DISARM` | Actuation gate |
| `SET_ACTUATOR_PROFILE:` | Load encoder profile name |
| `SET_SPEED:` | Speed interlock input (km/h) |

**stdout**

| Prefix | Payload |
| :-- | :-- |
| `TELEMETRY:` | `ActuationTelemetryV1` JSON (authority, interlocks, last mask) |
| `[ACTUATION_LOG]` | Diagnostics |

## Run (Phase 1)

```powershell
pip install -r requirements.txt   # no packages required today

python actuation_engine.py

# Bench smoke (synthetic bitmap pump)
python bench/actuation_smoke.py

# Phase 2 — vision + actuation cross-process pipeline
python bench/pipeline.py --duration 8
```

Manual pipe (workshop, no shell):

```powershell
# Terminal 1
python C:\Projects\PUFworks-vision\vision_engine.py --synthetic

# Terminal 2 (future)
python actuation_engine.py
# paste: SET_CONTROL_AUTHORITY:SHADOW, START, then forward SECTION_BITMAP lines
```

## Boundaries

- **No vision / OpenCV / GoB** — subscribe to `SectionBitmapV1` only.
- **No ISOBUS address claim** — use `PUFworks-isobus` for ISO 11783 / J1939 CAN paths (experimental and machine-specific — not a certified multi-brand guarantee).
- **No direct laptop GPIO to solenoids** — MCU owns coil drivers and local fail-safe.
- **No agronomy / dataset capture** — offline only in `PUFworks-agronomy`.

## Related docs

| Doc | Location |
| :-- | :-- |
| Integration seam | `INTEGRATION_SEAM.md` |
| Implementation plan | `Plans/PLAN.md` |
| Vision output plan | `PUFworks-vision/Plans/SectionOutput/PLAN.md` |
| Contracts | `PUFworks-contracts/schemas/section_bitmap.v1.json` |
| ISOBUS safety (reference) | `PUFworks-isobus/SAFETY.md` |

---

```
                              .=*****=. :@@=                                    
                            .----.-%@@@@@#=@@@= .-                              
                            .:=@@@@@@@@@@@@@@@@@.*@:                            
                       *@@@@@@@@@@@@@@@@@@@@@@@@@=@@:                           
                    *@@*+@@@@@@@@@@@@@@@@@@@@@@@@@@@@                           
                    .=@@@@@@@@%++++++++#%@+%@@%#@@@@@.                          
                   *- #@@@@*+*%@@@@@@@#++++++@@*%@@@@@@*:                       
                    -@@@@++#@%*+++++++%@++++++@#*@@*+*@@@@@@+                   
                   *@@@#++%++*%@@@@@@#++#+++++*%+@%++#@*++*@@*                  
                  +@@@*++++#@@@%+-=%@@@%+++++++%+#+++*++*%%*@#                  
                  @@@*++++@@@.        %@@++++++*++++++*@@@@@@#                  
                 +@@%++++%@*           *@%+++++++++++*@#   .@@.                 
                .%@@+++++@%   =@@#      @@+++++++++++@#     -@#                 
             .=@@@@+.=+++@+ .@@@*  .    =@+++++++++++@.%@:  .@#                 
         -@@@@@@%.   .+++@* =@@@@@@%    +@++++++*%%#+@@@@:+ .@#                 
            -@@:-%.    -+@@.-@@@@@@*    @#+++*@#++++%@@@@@% -@+                 
           :@@#@#    --+##@@:=@@@@%.   @%+++%+++++++++@@@@=:@%                  
          .@@@@@.   .%++++++%@+.    .#@++++++++++++++++@@.+@@@@.                
          .@@@@#    =+++@@#+++++#%%#++++++++++++%%*++++++@@*+#@+                
          .@#*@% .  :+++++#@@%*++++++++++++++++*@@@@%++++++%@@#                 
           : =@@#%.  .=+++++#@@#@@@@%#*++++++++++++++++++++++#@%.               
              +@@@@:    .-+++++%@#. .:=#@@@@@@%#*++++++++++++++@@:              
               :@@@@@-     .*+++++%@@#-   .....=%@@@@@%++++++++%@=              
                 . .@@@@%:. .@%++++++*%@@#:...-=*##*%@@@@@@#+++@@-              
                    #@@%.    :@@@@%*+++++++*%%%%%#*+++#@@@@@@@@@*               
                    :@@@=     +@@@@@@@@@%#**+++++*#@@@*     .%#.                
                     #@@@.     #@@@@@@@@@@@@@@@@@@*:                            
                      %@@%     .%@@@@@@@@@@@@-                                  
                      .@@@#      %@@@@@@@@@@@                                   
                       #@@@=      #@@@@@@@@@@-                                  
                        @@@@.      =@@@@@@@@@*                                  
                         @@@%        %@@@@@@@%                                  
                         .@@@#        .@@@@@@@=                                 
                          -@@@+         :@@@@@%                                 
                           #@@@:          .@@@@=                                
                           .@@@%            +@@@:                               
                            .@@@*            %@@%                               
                             -@@@=           .@@@*                              
```
