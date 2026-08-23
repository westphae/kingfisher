# Board caliper sheet (fill in)

Measure the actual boards before the next layout. The model currently treats
every board as a bare rectangle with four corner holes and one component
height, which is why print-3 could not be wired: **nothing in the script knows
a connector exists**, so it cannot leave room for a cable.

Companion sheets: [`SUN_B_CALIPERS.md`](SUN_B_CALIPERS.md),
[`BATTERY_CALIPERS.md`](BATTERY_CALIPERS.md). Both caught things the datasheets
did not; the public docs for these boards give no connector positions at all.

**One number per row.** Where a board has a variable number of holes or
connectors, use the matrix tables — add or delete rows as needed.

## Coordinate convention

Boards mount flat against the plate's land, in the **X–Z plane**:

```
        v (up, +Z)
        ^
   W    |  .-----------------------------.
        |  |                             |
        |  |   component side faces -Y   |   <- toward the seam / bowl
        |  |   (out of the page)         |
   0    |  '-----------------------------'
        +--------------------------------> u (aft, +X)
           0                              L
```

- **u** runs fore → aft; **v** runs down → up. Origin is the **forward-bottom**
  corner as the board sits in the pod.
- **L** is the board's size along u. **W** is its size along v.
- Edges are **fore** (u=0), **aft** (u=L), **down** (v=0), **up** (v=W).
- `at` means the distance along that edge from the low end (from u=0 for a
  fore/aft edge; from v=0 for an up/down edge).

## How to measure

1. Wipe the jaws, close fully, press **ZERO**.
2. Hole **centres** are easiest as edge-to-hole-edge plus the radius.
3. **Component height** is the tallest thing standing off the component face,
   with the mating cable fitted if that is what will be installed.
4. **Cable room** is the one people skip and the one that bit us: with the
   cable plugged in, how far does it stand off the board edge **before it can
   bend**? Include the plug body and the strain relief. Usually 8–12 mm for a
   Qwiic lead — measure rather than assume.
5. Take critical numbers twice; if they differ by more than 0.2 mm, take a
   third and keep the middle.

## What each number drives

| You give me           | The model does                                                     |
|-----------------------|--------------------------------------------------------------------|
| `L`, `W`              | board outline, and the footprint that must not overlap a neighbour |
| hole `u`, `v`         | **only** these standoffs get printed — no invented holes           |
| component height      | inboard keepout depth, and how deep in Y the board can sit         |
| back-face height      | whether the board sits flat on the standoffs                       |
| connector edge + `at` | where the cable leaves, so the envelope grows there                |
| cable room            | clear space reserved beyond that edge                              |

---

## 1. Holybro MS4525DO carrier

Assumed today: L 22.9, W 17.0, component height 12.0, **four** holes — wrong,
you report two: forward-top and rear-bottom.

| ID | Measure                                               | Value (mm) |
|----|-------------------------------------------------------|------------|
| A1 | Outline **L** (along u, fore→aft)                     | 22.9       |
| A2 | Outline **W** (along v, down→up)                      | 17.0       |
| A3 | PCB thickness                                         |  1.6       |
| A4 | Tallest component height, component face (−Y)         |  9.9       |
| A5 | Tallest feature on the **back** face                  |  1.6       |
| A6 | Which feature faces **forward** in the pod (describe) |  barbs     |

Mounting holes:

| # | u | v | Ø |
|---|---|---|---|
| 1 |  2.50 | 14.50 | 3.00 |
| 2 | 14.50 |  2.50 | 3.00 |

Connectors and ports:

| Name             | Edge |  `at` | Body width | Body height | Cable/port room |
|------------------|------|-------|------------|-------------|-----------------|
| pitot port       | fore | 11.00 | 3.5        | 6.5         | 7.0             |
| static port      | fore |  5.00 | 3.5        | 3.0         | 7.0             |
| JST (electrical) |  aft | 11.50 | 6.1        | 4.0         | 5.0             |

## 2. SparkFun Qwiic 5V Boost

Assumed today: L 24.5, W 24.5, component height 8.0, four corner holes.
You report **two Qwiic connectors on opposite sides**, so it must mount with
them fore/aft.

| ID | Measure                          | Value (mm) |
|----|----------------------------------|------------|
| B1 | Outline **L**                    | 25.25      |
| B2 | Outline **W**                    | 25.25      |
| B3 | PCB thickness                    |  1.60      |
| B4 | Tallest component height         |  3.00      |
| B5 | Tallest feature on the back face |  0.40      |
| B6 | Which feature faces **forward**  |  JST       |

Mounting holes:

| # | u | v | Ø |
|---|---|---|---|
| 1 |  2.50 |  2.50 | 3.00 |
| 2 |  2.50 | 22.75 | 3.00 |
| 3 | 22.75 |  2.50 | 3.00 |
| 4 | 22.75 | 22.75 | 3.00 |

Connectors:

| Name     | Edge | `at`  | Body width | Body height | Cable room |
|----------|------|-------|------------|-------------|------------|
| Qwiic 1  | fore | 12.63 | 6.00       | 3.00        | 4.00       |
| Qwiic 2  |  aft | 12.63 | 6.00       | 3.00        | 4.00       |
| VIN/VOUT |   up | 12.63 |            | 4.00        | 4.00       |

## 3. SparkFun Pro Micro ESP32-C3

Assumed today: L 33.0, W 17.8, component height 8.0, four holes — there are
**none**. SparkFun document castellated headers along both long edges and no
mounting holes, which is why it sits in `pm_tray.stl`.

| ID  | Measure                                                      | Value (mm) |
|-----|--------------------------------------------------------------|------------|
| C1  | Outline **L**                                                | 33.51      |
| C2  | Outline **W**                                                | 17.70      |
| C3  | PCB thickness                                                |  0.78      |
| C4  | Tallest component height, component face                     |  4.40      |
| C5  | Tallest feature on the back face                             |  0.00      |
| C6  | Which short end carries the **USB-C** (fore or aft)          | aft        |
| C7  | USB-C centre position along that end (`at`, from v=0)        |  8.85      |
| C8  | How far the USB-C **plug** protrudes past the board edge     |  1.30      |
| C9  | Clearance needed off a long edge to solder a castellated pad |  2.00      |
| C10 | Must Reset/Boot be reachable after assembly? (y/n)           |  n         |

Connectors:

| Name  | Edge | `at` | Body width | Body height | Cable room |
|-------|------|------|------------|-------------|------------|
| USB-C | aft  | 8.85 | 9.00       | 3.20        | 30.00      |
| Qwiic |  up  | 0.00 | 2.60       | 6.00        |  7.00      |

## 4. SparkFun Battery Babysitter (PRT-13777)

Assumed today: L 33.0, W 33.0, component height 8.0, four corner holes.

| ID | Measure                                               | Value (mm) |
|----|-------------------------------------------------------|------------|
| D1 | Outline **L**                                         | 32.90      |
| D2 | Outline **W**                                         | 33.09      |
| D3 | PCB thickness                                         |  1.60      |
| D4 | Tallest component height                              |  5.52      |
| D5 | Tallest feature on the back face                      |  0.50      |
| D6 | Which feature faces **forward**                       |  i2c wires |
| D7 | Must JP12 / SYSOFF be reachable after assembly? (y/n) |  no        |

Mounting holes:

| # | u     | v     | Ø    |
|---|-------|-------|------|
| 1 |  2.60 |  2.60 | 3.29 | 3.29 0.96
| 2 |  2.60 | 30.49 | 3.29 |
| 3 | 30.30 |  2.60 | 3.29 |
| 4 | 30.30 | 30.49 | 3.29 |

Connectors:

| Name        | Edge | `at`  | Body width | Body height | Cable room |
|-------------|------|-------|------------|-------------|------------|
| battery JST | up   | 19.20 | 8.00       | 5.53        |  4.00      |
| load / VOUT | fore | 16.55 | 9.20       | 3.00        |  4.00      |
| USB-B       | aft  |  8.80 | 7.95       | 2.70        | 50.00      |

## 5. SparkFun BMP581 (Qwiic) — inside the sealed plenum

Assumed today: L 25.4, W 25.4, component height 8.0, four corner holes.
**Pass-through wiring agreed: two cables, two glands**, so both connector
positions matter — they set where the cup's sealed entries go.

| ID | Measure                                             | Value (mm) |
|----|-----------------------------------------------------|------------|
| E1 | Outline **L**                                       |  25.31     |
| E2 | Outline **W**                                       |  25.18     |
| E3 | PCB thickness                                       |   1.54     |
| E4 | Tallest component height                            |   3.08     |
| E5 | Tallest feature on the back face                    |   0.00     |
| E6 | Sensor port: how far it stands off the board        |   0.50     |
| E7 | Sensor port: which face it is on (component / back) | component  |
| E8 | Which feature faces **forward**                     | qwiic 1    |

Mounting holes:

| # | u     | v    | Ø    |
|---|-------|------|------|
| 1 |  2.55 | 2.55 | 3.29 |
| 2 | 22.76 | 2.55 | 3.29 |

Connectors:

| Name    | Edge | `at`  | Body width | Body height | Cable room |
|---------|------|-------|------------|-------------|------------|
| Qwiic 1 | fore | 12.59 | 5.96       | 3.06        | 4.00       |
| Qwiic 2 | aft  | 12.59 | 5.96       | 3.06        | 4.00       |

## 6. SparkFun MMC5983MA (Qwiic)

Assumed today: L 19.0, W 7.6, component height 8.0, two holes.

| ID | Measure                          | Value (mm)  |
|----|----------------------------------|-------------|
| F1 | Outline **L**                    | 19.13       |
| F2 | Outline **W**                    |  7.52       |
| F3 | PCB thickness                    |  1.63       |
| F4 | Tallest component height         |  3.02       |
| F5 | Tallest feature on the back face |  0.00       |
| F6 | Which feature faces **forward**  |  mount hole |

Mounting holes:

| # | u    | v    | Ø    |
|---|------|------|------|
| 1 | 2.53 | 3.66 | 3.12 |

Connectors:

| Name  | Edge | `at` | Body width | Body height | Cable room |
|-------|------|------|------------|-------------|------------|
| Qwiic | aft  | 3.76 | 5.96       | 3.06        | 4.00       |

---

## Status

- [x] Measured (date: 2026-08-23)
- [ ] `BOARD_SPECS` populated; every unmeasured value still marked assumed
- [ ] `validate_pod.py` green with **I6′** (connector envelopes) and **I9**
      (no invented holes)
