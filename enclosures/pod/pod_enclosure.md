# `wing_pod_v3.py` — wing air-data pod (clamshell)

Parametric CadQuery model of the under-wing air-data pod. **v3** is a complete
redesign of the v2 shell; see "Why v3" below for what actually went wrong.

**Non-negotiables:** see [`REQUIREMENTS.md`](REQUIREMENTS.md). After every
geometry change, regenerate and run the validator (must exit 0):

```bash
cd enclosures/pod && uv run --project .. python wing_pod_v3.py
cd enclosures/pod && uv run --project .. python validate_pod.py
```

## Why v3

v2 built the outer mold line as `mid ∪ nose ∪ tail ∪ panel-pad` — four solids
fused by boolean. Two classes of defect followed from that:

- **The +Y service panel was an open hole.** `PANEL_Y` was set flush to the
  construction ellipse at the *bottom* of the rocker cut (z=59.5) while the pad
  spanned z=57.5…74.5, where the ellipse had receded to y=33.4 — so an 18 mm
  slab sat up to 6 mm proud of the skin. Worse, the pad used the **same** x/z
  rectangle for the outer body and the inner cavity, so `outer.cut(inner)` left
  its perimeter with zero wall: an ~80 × 3.5 mm slot into the electronics bay
  under the flat top, plus open slots at both X ends. The old seal check missed
  it because its leak grid only sampled six z levels near the bottom fairing.
- **The shape was a brick.** 216 mm long with a 28 mm nose and a **12 mm** tail
  on a 52 × 77 section: fineness ~3.4 and a ~3500 mm² near-flat base.
  `BOTTOM_EDGE_R` was disabled outright because a post-hoc fillet would not fuse
  with the ogives, and both fairings had been trimmed to satisfy an unstated
  bed-diagonal budget.

v3 makes both impossible rather than fixed:

- The whole OML is **one loft over one parametric section family**
  (`section_wire`). Nose, midbody and boattail are stations of that family, and
  the inner cavity is the *same call* with `inset=WALL`. A footprint mismatch
  like the v2 pad cannot be expressed. Junction steps cannot occur, so the tips
  now sit on the seam and `BOTTOM_EDGE_R` is section-native.
- The service cluster moved to the **aft face**, on a bolted, gasketed plate.
  Nothing pierces the +Y skin except the static array, so there is no local pad
  to come adrift — and the cuts sit in the base wake, the lowest-pressure and
  lowest-impingement surface on the body.
- The bed constraint is now **P0**, stated with its arithmetic, instead of being
  paid for silently out of the tail.

## Shape

~**235 × 52 × 61 mm**. Flat top (wing-fairing mate, full length aft of the
nose), flat bottom forward, radiused bottom edges, rounded-rectangle sections
that degenerate to a circle at the nose mouth.

| Station | x | |
|---|---|---|
| Nose fairing | 0 → 52 | mouth on the SUN Ø10.65 barrel, grows to full section |
| Midbody | 52 → 165 | constant; boards, battery, static bay |
| Boattail | 165 → 235 | upswept bottom + tapered sides |
| Base | 235 | 37 × 52, carries `tail_panel.stl` |

Measured: fineness **3.71** (D_eq 62.7 mm), nose 0.82 D, boattail 1.11 D, max
boattail surface angle **10.9°**, base/frontal **0.61**.

Fineness is payload-limited, not shape-limited: `OUTER_H` is set by the 50 mm
battery between the flange rails and `OUTER_W` by the board land. Base/frontal
0.61 is the honest price of an aft-facing service panel — the cluster needs
~28 × 41 mm of flat. The long, gentle boattail is what keeps base pressure
recovered.

## Coordinate system

Origin at the outer nose tip on the seam / bottom. **+X aft**, **+Y right**,
**+Z up** (flat top at `OUTER_H`). Split plane is **Y = 0**.

Print STLs drop on the bed as-exported — **flange down, rotated 45°, and
centred on the bed at (110, 110)**. No slicer rotation *or repositioning*: a
corner-parked part puts its skirt/brim at negative coordinates and the slicer
rejects the job. The `.step` files carry the same print orientation as the
`.stl` (v2 kept STEPs in model orientation; v3 does not).

## Exports

| File | Role |
|------|------|
| `pod_left.stl` / `.step` | Left half (cover + pitot cradle), print-oriented |
| `pod_right.stl` / `.step` | Right half (electronics), print-oriented |
| `tail_panel.stl` / `.step` | Aft service plate: rocker, USB, two LED holders |
| `static_cover.stl` / `.step` | Closes the BMP bay tool window after heat-set |
| `pm_tray.stl` / `.step` | Pro Micro clamp tray (no OEM holes) |
| `pod_assembly.step` | Assembled + SUN-B placeholder |
| `pod_v3_*.png` | QC previews — unique name per revision |

## Interior, nose → aft

1. **SUN-B cradle** on the centreline, protruding 45 mm (see below).
2. **Midbody**, boards in four columns on the +Y wall land, two rows in Z where
   the pair fits the 56 mm interior:

   | Column | x | Lower row | Upper row |
   |---|---|---|---|
   | A | 44 → 67 | MS4525 (z 4) | MMC5983 (z 26) |
   | B | 71 → 104 | Qwiic Boost (z 4) | Pro Micro on `pm_tray` (z 33.5) |
   | C | 108 → 140 | isolated static bay: BMP581 (z 16) | |
   | D | 144 → 177 | Battery Babysitter (z 12) | |

   Battery slab **68.5 × 5.9 × 49.3, laid down**, on the seam at x 85 → 155.5,
   measured 2026-08-18 ([`BATTERY_CALIPERS.md`](BATTERY_CALIPERS.md)), leads on
   the **aft** edge — the only edge with more than 2.5 mm of clearance. The
   thickness across the flats sets `OUTER_H` and therefore, via P0, the length
   too; it is left with 1.7 mm of spare rather than sized to the measurement,
   because pouch cells swell.
3. **Boattail** — empty taper, drain at its forward end, service plate at the base.

## Pitot mount (ESA SUN-B)

Calipers: [`SUN_B_CALIPERS.md`](SUN_B_CALIPERS.md).

v3 protrudes **45 mm** and clamps the **Ø11.76 threaded band** — the feature ESA
put there for mounting — instead of v2's 21.25 mm on the Ø8.93 shoulder:

- Nose mouth sits on the Ø10.65 smooth barrel, with ≥1.5 mm of PETG at the lip
  (print-1: a 0.7 mm lip tore off the left half).
- Forward stop is the **Ø10.65 → Ø11.76 step** landing on the nose bulkhead's
  aft face at x = 7.25. That is a *larger* step than the shoulder it replaces.
- Integral split clamp on the thread, x 7.25 → 32.6.
- Aft blind recess (Ø6.03 × 7.06) on a locating boss **4.56 mm** long, so ≥2.5 mm
  of the cup stays unused (print-1: a boss ~2 mm too long held the SUN off its
  stops).
- Barbs **up** (ESA water tip) at x 40 / 55 / 69, tips at z 39.9 with ~17 mm of
  headroom to the interior ceiling for the 6 mm hose to turn aft.

This is what pays for the boattail: it moves the SUN aft face from x=103 to
x=79, and the freed 24 mm goes into the tail.

## Pneumatics

| Line | Source | Destination |
|------|--------|-------------|
| Total | SUN-B **aft** barb | 6 mm hose → COTS reducer → MS4525 `+` |
| Static (airspeed) | SUN-B **middle** barb | 6 mm hose → COTS reducer → MS4525 `−` |
| TE | SUN-B **forward** barb | Capped / unused (Prandtl) |
| Static (baro) | Pod multi-hole side array at **53 % of body length** | Isolated BMP581 bay only |

## Aft service panel (L6)

| Role | P/N | Cut | Notes |
|------|-----|-----|-------|
| Switch | SparkFun COM-08837 (E-Switch R1966A) | 19.6 × 13.0 snap-in | Plate is 3.0 mm — inside the 2.0–3.0 mm band, no rebate. SPST to **JP12 (SYSOFF)** + GND; leave onboard S1 OFF |
| USB | SparkFun CAB-15464 | 10.5 × 7.5 window + M3 Ø3.3 at 17 mm | Pigtail to the Babysitter; RTV under the flange; nuts behind the plate |
| LEDs | 5 mm chrome ABS holders | 2 × Ø8.2 | **red** = VOUT (pod powered), **blue** = parallel D1 / `!CHG!` |

Stacked in Z on the base: rocker at z 48, USB at z 33, LEDs at z 20. All four
plate screws are M2.5 into inserts on the **right** half — an insert boss runs
along X and would straddle the seam near y=0, so they sit at y ≥ 5.5 and the
clamshell flange runs aft to x=229 to hold the left tail.

### Babysitter wiring (PRT-13777)

The onboard slide switch is **S1**: pole `P` = SYSOFF, throw `S` = GND. ON pulls
SYSOFF to ground. OFF lets it float high (BQ24075 disconnects the battery from
the load). **USB still powers VOUT with the switch OFF** (power-path), and
charging requires the switch ON. There is no 0.1″ "EXT SW" header; there *is* an
unsilkscreened PTH **JP12** on SYSOFF.

Onboard LEDs (both anodes on VOUT): **D1** blue = `!CHG!` via R9; **D2** red =
`!PGOOD!` via R10 / SJ5 — that is *valid USB/VIN*, not "battery supplying the
load". So the remote red "pod powered" LED is VOUT → resistor → LED → GND, not
a parallel of D2.

## Drain (L8)

Labyrinth at the aft end of the flat bottom, the low point of the cavity, clear
of the battery and the flange rails: cavity → floor-level slot in the aft end
wall → 8 mm channel → down through the skin. No straight path from freestream
to interior, so it sheds condensate and equalises pressure without acting as a
ram-air inlet.

## Deferred

- **Pod↔wing fairing latch** and the inspection-plate mount. The flat top deck
  is the mate for a separate printed fairing; nothing about it is in this script.
- **U.FL antenna** grommet — PCB antenna is fine for early RF testing.
- `MS_TUBE_OD = 3.5` is still marked `(VERIFY)`.

## Archive

`wing_pod_v2.py` (and `../KingfisherPod.zip` for the v1 insert-style tray) are
superseded. Do not evolve them; tune v3 params instead.
