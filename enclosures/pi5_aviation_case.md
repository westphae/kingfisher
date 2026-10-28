# `case/pi5_aviation_case.py` — hub enclosure script notes

Parametric CadQuery model of the cabin "hub" enclosure. **v4** (2026-10-19)
is a tray and a hood. The base is a plate: stack pads, GPS and IMU pedestals,
and a short right bulkhead under the Ethernet/USB shells (the SMA nut lands
on that same wall). The hood is the lid plus the front, back, and left walls,
full-height right corner legs, and the band above the ports. **v3** (2026-07-17)
was the tub that housed the same stack: Geekworm **X1200 UPS** (18650s down) →
Raspberry **Pi 5** (pogo-pin powered) → Active Cooler → official **M.2 HAT+**
on 16 mm standoffs → IDC ribbon connector on extended GPIO pins — plus the
SparkFun NEO-M9N GPS and ICM-45686 IMU in a rear bay. One script generates
`case_base.{step,stl}`, `case_lid.{step,stl}` (the hood, print-oriented), and
`case_assembly.step`.

- Printer/material: AnkerMake M5C, PETG. Fasteners: M2.5 heat-set inserts
  (bay pedestals + four hood corner bosses). The stack mounts on its own
  metal standoffs. The hood is four **M2.5×10 countersunk** screws up through
  the plate into those bosses.
- Outer envelope: **97.0 × 113.1 × 76.2 mm** to the flat lid top.
- Hardware measurements + mounting rationale: **`x1200_m2hat_stack.md`**.
- Archives: `KingfisherCase.zip` = v1, `KingfisherCase2.zip` = v2 as printed
  (85×112×35, Pi + bay only, incl. the old `relief.png` render),
  `KingfisherPod.zip` = wing-pod **v1** insert (archived). Live pod model:
  [`pod/wing_pod_v3.py`](pod/wing_pod_v3.py) / [`pod/pod_enclosure.md`](pod/pod_enclosure.md).
- **OLED lid (2026-10-23):** SparkFun Qwiic OLED 1.3″ (LCD-23453, 128×64) over the
  GPS bay, glass flush with the flat lid top, GPIO button to the +X side (toward
  the IMU). The through opening is the measured glass plus 1 mm in X and in Y
  (35.54 × 25.13 mm) after the 34.54 × 24.13 mm hole printed tight. No visor:
  the outer face sits on the bed. QC preview: `case/case_v4_oled_win_20261023.png`.
  `case/case_v4_oled_flat_20261021.png` is the tight glass-size opening.
  `case/case_v4_oled_flush_20261020.png` is the visor revision.
  `case/case_v4_tray_hood_20261019.png` is the earlier lid (cable slot, guessed
  pocket). `case_lid_oled_qc_20260821.png` is the v3 lid only.

## OLED lid IDs

| ID | Requirement |
|---|---|
| O1 | Through opening 35.54 × 25.13 mm, centered: measured glass 34.54 × 24.13 mm plus 1 mm in X and in Y. 34.54 mm is case X (datasheet panel 34.5); 24.13 mm is case Y (panel 23.0). The opening stays inside the PCB outline. Countersinks stay ≥0.5 mm off the opening |
| O2 | Underside pocket for the ear outline 35.56 × 36.83 mm + 0.4 mm. Depth seats an assumed 1.6 mm FR4 so the measured 4.52 mm stack is flush with the lid top (pocket 1.08 mm; the board hangs 0.52 mm below the underside) |
| O3 | No outside Qwiic opening. The jacks are on the back of the board and face the GPS bay; plug a long cable from inside before the hood goes on |
| O4 | Tactile button hole Ø7.2 mm, +X of the PCB (toward the IMU), clear of lid posts and flank vents |
| O5 | Window must not overlap cooler intake grille (`INTAKE_R`) |
| O6 | Keep flanking 3×22 mm exhaust slots left/right of the bezel |
| O7 | Outer lid face is flat. No visor around the window, so that face sits on the bed when the hood is printed lid-down |
| O8 | Four M2.5×8 countersunk bolts through the ears (centres ±15.24 X, ±15.875 Y), Ø3.4, nut under the ear. The ear hole is a #4 clearance (3.30 mm); the board locates the screw |

Wiring: Qwiic joins Pi I²C-1 (IMU 3.3 V / GND / SDA / SCL until the HAT has a jack), not the NEO-M9N DDC jack. Cut the OLED I²C pull-up jumper. The cable plugs into the jack on the back of the board and hangs in the GPS bay. Button is GPIO4 / pin 7 to GND pin 9 (active-low).

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
| Clearances | `CLR_L=6` (FPC ribbon bulge + Pi/X1200 buttons), `CLR_F=4.3` (hood corner bosses clear the board front), `IO_CLR=0`, `BAY_GAP`, `BACK_CLR` | |
| X1200 walls | `UPSC_*` (charge window + pilaster on the **hood** front wall), `BTN_*`, `LED_*` | Pi USB-C has no hole. Back-edge LEDs face the bay — unpiped |
| Hood joint | `GROOVE_*`, `LAP_*`, `HOOD_BOSS_H`, `CSK_*` | plate groove, half-lap on the bulkhead, countersunk hood screws |
| Inserts/bosses | `INSERT_*`, `BOSS_D`, `POST_OFF` | bay pedestals on the plate; four short bosses on the hood |
| Pi/IO | `PI_*`, `ETH_FROM_PI`, `USB2_BACK_FROM_PI`, `IO_WIN_H` | bulkhead runs Ethernet-front to the same rear inset; sill at `PI_BOT_Z` |
| Bay | `GPS_*`, `IMU_*`, `SMA_*`, cable clips (`INSTALL_CLIPS=False`) | unchanged from v2; **designed to be deletable wholesale** if a custom GPS/IMU HAT happens later |
| Vents | `INTAKE_*` (lid grille over cooler), `SLOT_W` + `vent_ys` | left-wall slots moved up to cooler level, two groups flanking the FPC keep-out |

## Guard asserts

The script self-checks on every run; keep these healthy when changing params:
cells-off-floor ≥1 mm; counterbore floor ≥2 mm; stack-to-lid headroom ≥2 mm;
FPC ribbon and button overhang inside `CLR_L`; hood corner bosses clear the
board front (`CLR_F=4.3`) and leave ≥1 mm before the Ethernet face; vertical
lip engagement ≥0.5 mm; SMA nut lands on the bulkhead; button hole above the
floor; vent slots outside the FPC keep-out; all v2 bay asserts (GPS↔IMU gap,
SMA position, clip spacing). After the solids build, `audit` checks H1–H8
(plate has no left wall, Pi USB-C is closed, charge port is open, sill and
SMA, port mouth, corner screw and insert, lip tongues, no volumetric clash).

## v4 tray and hood

The stack, GPS, IMU, and SMA go on the plate with the hood off. The right
edge still meets the bulkhead (`IO_CLR=0`); the Ethernet and USB shells sit
above the sill, so they are not threaded into a window. The left wall, and
the X1200 button in it, is on the hood.

- **Bulkhead** Y from the Ethernet front face (`BULK_FRONT`, 8.3 mm) to the
  same inset at the rear (`BULK_BACK`). Top at the Pi underside. SMA hole
  unchanged, with ≥6 mm of wall behind it for the nut.
- **Hood right side:** full-height corner legs about 8 mm long, a band above
  the shells (`PORT_TOP`), and a wall down onto the sill aft of USB2. The
  1.3 mm between the corner boss and the Ethernet is the vertical half-lap
  (about 0.7 mm of engagement after `LAP_CLEAR`).
- **Other walls** drop a skirt into a 1.2 mm groove in the plate. The groove
  stops 8 mm from each corner so it stays clear of the countersinks.
- **Screws** are under the Dual-Lock face, with the stack screws. Heads are
  flush. The hood comes off after the box is lifted off the panel.
- **Pi USB-C** is solid wall. Charge only through the X1200 window. The
  pilaster still brings the frame to 0.3 mm off the board, with a sill chamfer
  so the hood can slide down over the jack.

## v3 design notes

- The stack is assembled outside the case (standoffs → X1200 → spacers → Pi →
  cooler → HAT standoffs → HAT → pins/IDC), then drops in and is fastened by
  4 screws from below.
- The lid grille sits above the HAT; fan air comes through the lid, around the
  HAT edges, into the cooler; exhaust via left-wall slots at cooler level and
  the (large, open) I/O window.
- microSD access dropped (NVMe boot). v3 kept a Pi USB-C cutout for bench use; v4 closes it.
- The X1200's fuel gauge (I2C 0x36) + power-loss GPIO run over the user's
  GPIO ribbon — a candidate future kingfisher battery device.
- Charging: X1200 USB-C is 5 V/5 A (2.3–3.2 A charge) — never from the Pi's
  own USB; see the VBUS over-current history in `~/.claude/CLAUDE.md`.
- Bench stack total measured 52.2 vs 53.3 summed — `STACK_HEADROOM=2.5`
  absorbs the disagreement in the safe direction.
