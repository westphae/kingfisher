# `case/pi5_aviation_case.py` — hub enclosure script notes

Parametric CadQuery model of the cabin "hub" enclosure. **v3** (2026-07-17)
houses the full stack: Geekworm **X1200 UPS** (18650s down) → Raspberry **Pi 5**
(pogo-pin powered) → Active Cooler → official **M.2 HAT+** on 16 mm standoffs →
IDC ribbon connector on extended GPIO pins — plus the SparkFun NEO-M9N GPS and
ICM-45686 IMU in a rear bay. One script generates `case_base.{step,stl}`,
`case_lid.{step,stl}` (print-oriented, flipped), and `case_assembly.step`.

- Printer/material: AnkerMake M5C, PETG. Fasteners: M2.5 heat-set inserts
  (bay + lid posts); the stack mounts on its own metal standoffs (below).
- Outer envelope: **97.0 × 113.1 × 76.2 mm** (base 72.2 + lid 4).
- Hardware measurements + mounting rationale: **`x1200_m2hat_stack.md`**.
- Archives: `KingfisherCase.zip` = v1, `KingfisherCase2.zip` = v2 as printed
  (85×112×35, Pi + bay only, incl. the old `relief.png` render),
  `KingfisherPod.zip` = wing-pod **v1** insert (archived). Live pod model:
  [`pod/wing_pod_v3.py`](pod/wing_pod_v3.py) / [`pod/pod_enclosure.md`](pod/pod_enclosure.md).
- **OLED lid (2026-08-21):** SparkFun Qwiic OLED 1.3″ (LCD-23453, 128×64) recessed
  in the lid rear over the GPS bay, facing up, with a GPIO button to the +X
  side of the panel (toward the IMU). QC preview: `case/case_lid_oled_qc_20260821.png`.

## OLED lid IDs

| ID | Requirement |
|---|---|
| O1 | Viewing aperture ≥ active area 29.42×14.7 mm + 1 mm (`OLED_WIN_CLR`) |
| O2 | Underside pocket for PCB 41.0×27.5 mm (datasheet panel 34.5×23.0; **caliper-tune `OLED_PCB_*` before print**) |
| O3 | Qwiic cable slot into the GPS bay; do not pinch on lid close |
| O4 | Tactile button hole Ø7.2 mm, +X of the PCB (toward the IMU), clear of lid posts and flank vents |
| O5 | Window must not overlap cooler intake grille (`INTAKE_R`) |
| O6 | Keep flanking 3×22 mm exhaust slots left/right of the bezel |
| O7 | 1.5 mm visor around the window (cabin glare) |

Wiring: daisy-chain Qwiic from the SparkFun NEO-M9N in the bay (cut the OLED I²C pull-up jumper). Button is GPIO4 / pin 7 to GND pin 9 (active-low).

## Regenerating

Python deps are managed with **uv** (workspace member `enclosures/`, CadQuery
2.8.0; see [`docs/python.md`](../docs/python.md)):

```bash
uv sync --all-packages   # from repo root → ./.venv
cd enclosures/case && uv run --project .. python pi5_aviation_case.py
```

Exports land in the CWD — run from `enclosures/case/` so STEP/STL sit beside
the script.

**nlopt:** `uv sync` currently pulls an `nlopt` wheel (incl. aarch64). If that
ever regresses, the old fallback was a source build into
`~/.venvs/cadquery` (cmake + swig); prefer fixing the lock/wheel first.

## Coordinate system & layout

Origin = outer bottom-left-front corner. X = Pi long axis, Y = depth (stack in
front/low-Y, GPS/IMU bay behind/high-Y), Z = up. `io(x, y)` maps interior →
global coords (adds `WALL`). Interior size derives from contents; **all deck
heights derive from the Z-stack parameters** — `PI_DECK_Z` (Pi PCB top) is the
master deck that wall cutouts follow automatically.

```
Z-stack (abs mm): floor 3 → pads 5 → [20 standoffs] → X1200 25..26.6 →
  [4.5 pogo spacers] → Pi 31.1..32.7 → cooler →46.7 → HAT 48.7..50.3 →
  IDC ribbon conn →69.7 → headroom → lid underside 72.2
```

Top view: unchanged from v2 — stack forward, bay (GPS left, IMU right, SMA
bulkhead low on the +X wall) behind a 4 mm gap.

## Stack mounting (no inserts)

Four M2.5 M-F **20 mm standoffs** thread into the X1200's threaded bosses from
below; M2.5×20 screws clamp Pi + pogo spacers from above; **case screws
(M2.5×8 pan) come up from outside the floor** through counterbored holes in
Ø11×2 raised pads, into the standoffs' female ends. Bottom face stays flat for
Dual-Lock. Scheme proven by Printables model 1288473 (same stack) and
Geekworm's X1200-C1 case. Loose standoffs → poor pogo contact.

## Parameter groups (v3)

| Group | Key params | Notes |
|---|---|---|
| Shell | `WALL=3`, `LID_T=4` | `WALL_H`/`OZ` are now **derived** from the stack |
| Z-stack | `PAD_H`, `UPS_STANDOFF_H=20`, `UPS_UNDERSIDE=18.5`, `POGO_SPACER_H=4.5`, `HAT_STANDOFF_H=16`, `IDC_*`, `RIBBON_ON_TOP`, `STACK_HEADROOM=2.5` | `RIBBON_ON_TOP=False` (discrete harness instead of IDC) shortens the case ~10 mm |
| Base screws | `BASE_HOLE_D`, `BASE_CB_D/DEPTH` | counterbored flush from below |
| Clearances | `CLR_L=6` (FPC ribbon bulge + Pi/X1200 buttons), `CLR_F=4.3` (**drop-in past the front lid posts**, was Ethernet-limited), `IO_CLR=0`, `BAY_GAP`, `BACK_CLR` | |
| X1200 walls | `UPSC_*` (front USB-C charge window + interior pilaster → plug seats ~flush), `BTN_*` (left Ø17 finger recess, button ~6 mm inside the face), `LED_*` (left bank slot + Ø3.2 charge-LED light-pipe hole) | back-edge LEDs face the bay interior — unpiped by design |
| Inserts/bosses | `INSERT_*`, `BOSS_D`, `POST_*` | unchanged from v2 (bay pedestals + floating lid posts) |
| Pi/IO | `PI_*`, `USBC_X`, `IO_WIN_*` | I/O window + Pi USB-C cutout track `PI_BOT_Z` |
| Bay | `GPS_*`, `IMU_*`, `SMA_*`, cable clips (`INSTALL_CLIPS=False`) | unchanged from v2; **designed to be deletable wholesale** if a custom GPS/IMU HAT happens later |
| Vents | `INTAKE_*` (lid grille over cooler), `SLOT_W` + `vent_ys` | left-wall slots moved up to cooler level, two groups flanking the FPC keep-out |

## Guard asserts

The script self-checks on every run; keep these healthy when changing params:
cells-off-floor ≥1 mm; counterbore floor ≥2 mm; stack-to-lid headroom ≥2 mm;
FPC ribbon and button overhang inside `CLR_L`; **drop-in**: the assembled
stack lowers past the floating front posts (`PIy0` vs post OD — this is what
pins `CLR_F=4.3`); front-right post vs Ethernet; button hole above the floor;
vent slots outside the FPC keep-out; all v2 bay asserts (GPS↔IMU gap, SMA
position, clip spacing).

## v3 design notes

- The stack is assembled outside the case (standoffs → X1200 → spacers → Pi →
  cooler → HAT standoffs → HAT → pins/IDC), then drops in and is fastened by
  4 screws from below.
- The lid grille sits above the HAT; fan air comes through the lid, around the
  HAT edges, into the cooler; exhaust via left-wall slots at cooler level and
  the (large, open) I/O window.
- microSD access dropped (NVMe boot). Pi USB-C cutout retained for bench use.
- The X1200's fuel gauge (I2C 0x36) + power-loss GPIO run over the user's
  GPIO ribbon — a candidate future kingfisher battery device.
- Charging: X1200 USB-C is 5 V/5 A (2.3–3.2 A charge) — never from the Pi's
  own USB; see the VBUS over-current history in `~/.claude/CLAUDE.md`.
- Bench stack total measured 52.2 vs 53.3 summed — `STACK_HEADROOM=2.5`
  absorbs the disagreement in the safe direction.
