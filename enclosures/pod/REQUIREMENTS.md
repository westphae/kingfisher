# Wing pod v3 — non-negotiable requirements

Source of truth for agents and humans editing `wing_pod_v3.py`.
After any geometry change: regenerate, then run `validate_pod.py` (must exit 0)
before calling an STL print-ready.

```bash
cd enclosures/pod && uv run --project .. python wing_pod_v3.py
cd enclosures/pod && uv run --project .. python validate_pod.py
```

## P0 — Build volume (governing constraint)

**This is the requirement that decides every other length in the design.** It
was only implicit in v2, and the cost of leaving it implicit was a 12 mm tail.

| ID | Requirement |
|----|-------------|
| P0 | Every printed part fits the **AnkerMake M5C** build volume **220 × 220 × 250 mm** with ≥10 mm margin in X/Y for skirt/brim — a rotated AABB of **210 × 210 × 250**. Body halves print flange-down and may be rotated **45° about Z**, so for a half of length `L` and height `H` the governing inequality is `(L + H)/√2 ≤ 210`, i.e. **`L + H ≤ 297 mm`**. Asserted at import and re-checked per exported solid. **Do not buy bed fit by silently shortening the nose or tail** — re-proportion the section, or split the part and say so. |

v2: 216 + 77 = 293 (and a 12 mm tail). v3: 235 + 61 = 296, 45° AABB 209.3 mm.

## Envelope & aero

| ID | Requirement |
|----|-------------|
| A1 | Overall **~235 × 52 × 61 mm** (L×W×H); asymmetric about the seam (thin left cover, wider right for the electronics land). `OUTER_H` is set by the battery pocket between the flange rails (see L1), `OUTER_W` by the board land. |
| A2 | The **entire outer mold line is ONE loft over ONE parametric section family** (`section_wire`). Nose, midbody and boattail are stations of that family. There is no `mid ∪ nose ∪ tail` fuse and there must never be one again. |
| A3 | Nose fairing ≥ **0.8 × D_eq**, boattail ≥ **1.1 × D_eq**. Nose mouth fairs into the SUN **Ø10.65 smooth barrel** with a print-durable lip (`NOSE_LIP_WALL` ≥ 1.5 mm radial — 0.7 mm PETG tore on the left half). Tips sit **on the seam**; v2 had to hold them off it to keep an OCCT fuse alive, and that constraint is gone. |
| A5 | **No planar freestream-facing butt** anywhere. This now holds *by construction*: the section laws are C1 (`_nose_law` has f'(1)=0, `_tail_law` has g'(0)=0), so the fairing↔midbody joins have no slope step to create one. |
| A6 | **Flat top** for the wing-fairing mate, running the full length aft of the nose: real chord width, genuinely planar (validated as planar faces at `z = OUTER_H`, not merely "near" it). Top edge may be square; a ~1.2 mm radius is kept so OCCT never sees a zero-length arc. |
| A7 | **Flat bottom** forward so the pod stands upright; the boattail sweeps it up. Bottom↔side edge carries an aero radius (`BOTTOM_EDGE_R`) that is **section-native** — a corner radius in the wire, not a post-hoc fillet. v2 disabled it entirely because a fillet would not fuse with the ogives. |
| A8 | Both halves reach a real nose tip (xmin ≈ 0). |
| A9 | Tail tip stays **fused** to each half (xmax ≈ `OUTER_L`); no dropped scrap, no flat nub. |
| A10 | The OML is **C1-continuous**: max slope jump across the section laws < 0.06. Validator samples 2000 stations. |
| A11 | Max boattail surface angle to the freestream ≤ **12°**. Each of the three tapers (left side, right side, bottom upsweep) is budgeted separately — a *proportional* split loads ~12 mm of the 15 mm width reduction onto the +Y side alone and measures 13.2°. |
| A12 | Report and bound **fineness ratio** (≥ 3.70) and **base/frontal** (≤ 0.70). Current: 3.71 and 0.61. Fineness is payload-limited, not shape-limited — `OUTER_H` is the 50 mm battery and `OUTER_W` is the board land. |

## Shell seal

| ID | Requirement |
|----|-------------|
| S1 | The closed L+R cavity has **no freestream openings** except: (1) Prandtl/SUN tip mouth, (2) multi-hole static array into the isolated BMP bay, (3) the **aft service panel** opening and its cluster cuts, (4) the **labyrinth drain**. **Nothing pierces the +Y skin except the static array** — the service cluster moved to the aft face precisely so the side skin has no cutouts and no local pad. No exterior battery door: the pack goes in from the open mating face before close-up. |
| S2 | Mating faces seal with an O-cord in the right-half groove and/or RTV on the flange. The groove **hugs the skin** (`WALL + GASKET_W/2`) so the battery notch cannot reach it. |
| S3 | Static-port holes open **only** into an isolated BMP plenum, formed by a **separate cup** (`static_bay.stl`) that seals to a flat land on the +Y wall with a foam/RTV gasket and four M2.5 into inserts (Qwiic gland in the cup). The BMP mounts and its inserts are heat-set on a **completely open wall** before the cup goes on. Integral walls plus a service window cannot do both jobs: the window must pass a heat-set iron yet leave a sealing frame, and at that board size it does neither — all four BMP bosses ended up behind the frame and the cover screws landed inside the board footprint. |
| W1 | **Minimum wall.** Stepping inward from the outer skin by `0.55 × WALL` along the surface normal must land in material at every sampled point, except inside a declared S1 opening. This is the direct gate on the v2 failure. |

## Interior must stay interior

| ID | Requirement |
|----|-------------|
| I1 | Flange rail, pitot bulkheads, electronics land, standoffs and static-bay walls are **intersected with the outer envelope** before union. No locating nubs as a board mount. |
| I2 | Built halves must not protrude outside `full_body_solid(0)` (extra volume ≈ 0). |
| I3 | Board mounts, static bay and static holes exist **only on `pod_right`**; left is cover + pitot cradle. Clamshell: heat-set inserts in the **right** flange; clearance + counterbore through the left. **Aft-panel inserts are also right-side only** — an insert boss runs along X and would straddle the seam if placed near y=0, so all four sit at y ≥ 5.5 and the flange runs aft to `OUTER_L − 6` to hold the left tail. |
| I4 | Mating face stays **open for install**, and the validator samples the cover's bay plus the battery and SUN insertion volumes to prove it. Every other check looks for *missing* material or material *outside* the envelope; none of them notices unwanted material sitting in the cavity. The flange is a **perimeter rail + local screw bosses**, not a bulkhead across the bay. |
| I5 | Boards **wall-mount** on the right +Y inner skin (XZ plane) on **raised standoffs** (PCB not flush on the land). Standoff/insert axes are **±Y** so heat-set irons enter from the open mating face — no floor posts in wells. |
| I6 | Each board's **3-D keepout** stays inside the cavity and clear of neighbours, the battery and the cradle, with `BOARD_GAP` between columns. **Every separately-printed part must also fit where it sits**: `pm_tray`, `static_bay` and `tail_panel` are built in assembly position and intersected against both halves. A part that is in neither half cannot foul either half until something checks — which is how `pm_tray`'s rim came to overlap the wall land by 1 mm. |
| I7 | **Every insert pilot must be physically reachable by a heat-set iron.** The validator sweeps a Ø`BOARD_POST_D` corridor along the iron's path — from the open mating face to the pilot for seam-access inserts — and fails if anything is in it. `insert_inventory()` in the model is the explicit list of all 44 inserts and the axis each is entered on. This is not a geometry property: the old static bay was a perfectly valid, watertight solid whose −Y frame simply left no corridor to the BMP's four pilots. Aft-panel inserts are entered from outside the base and are free by construction. |
| I8 | **Every board** mounts with M2.5 screws into heat-set inserts on standoffs (`STANDOFF_H` ≥ 4 mm), ≥2 insert positions each. The validator **fails** an empty `holes=[]` — that skip is how v2's wall-mount rework silently dropped the Pro Micro's fasteners. |

## Print / export (AnkerMake M5C)

| ID | Requirement |
|----|-------------|
| P1 | Each of `pod_left.stl`, `pod_right.stl`, `tail_panel.stl`, `static_bay.stl`, `pm_tray.stl` is a **single solid**. |
| P2 | Each STL is **watertight** (zero boundary edges after tessellation). |
| P3 | Tessellation tight enough for P2 (`tol ≤ 0.03`, `ang ≤ 0.05`). |
| P4 | Body halves export **flange/mating-face down**, curved outer up, **rotated 45°** for the bed diagonal. Signs are per-side; v2 shipped them swapped once and printed the flange on top. |
| P5 | SUN-B protrudes **45 mm** and is stopped by the Ø10.65→Ø11.76 step on the nose bulkhead's aft face. The aft pin must not bottom in the cup before that step seats: `SUN_RECESS_BOSS_LEN` ≤ cup depth − 2.5 mm (print-1: 0.5 mm extra was ~2 mm too long). |
| P6 | **All printed-joint screws** (clamshell, board, tray, static cover, aft panel) are **M2.5 into heat-set inserts** with **screw relief deeper than the insert**. No self-tap into PETG. LED holders use their own jam nuts. CAB-15464 USB ears are **M3 through-holes Ø3.3 + nuts behind the plate**. Rocker is snap-in. |
| P7 | Nose lip survives print/handling (`NOSE_LIP_WALL` ≥ 1.5 mm). |
| P8 | `static_bay.stl` is a single watertight solid, and prints **closed face down** so its flange tabs are a short overhang rather than a 29 mm bridge. |
| P9 | `pm_tray.stl` is a single watertight solid. |
| P10 | `tail_panel.stl` is a single watertight solid and prints flat, no supports. |

Slicer settings live in [`PRINT_RECIPE.md`](PRINT_RECIPE.md) — not in the STLs.

## Layout & pneumatics (do not silently change)

| ID | Requirement |
|----|-------------|
| L1 | Battery **68.5 × 5.9 × 49.3 mm** (X×Y×Z), **measured** 2026-08-18 ([`BATTERY_CALIPERS.md`](BATTERY_CALIPERS.md)) — **laid down**, not standing. Leads exit the **aft** edge, the only one with more than 2.5 mm of clearance. Y is thin across the seam. Pocket adds 1 mm/side in X/Y and 0.5 mm/side in Z. **The pocket must clear the flange rails**: usable height is `OUTER_H − 2×(WALL + BATT_SEAL_KEEP)`, and that inequality is what actually sets `OUTER_H`. The pocket notches interior material only and never reaches the sealing land or a screw boss. |
| L2 | Pitot: **ESA SUN-B**, protruding **45 mm**, clamped on its **Ø11.76 threaded band** — the feature ESA put there for mounting. Caliper ODs are the source; printed bores add per-station FDM allowance (print-1: 0.20 mm radial slip was OK on the aft barrel; 0.08 mm on the clamp was far too tight). |
| L3 | SUN aft barb → pitot → MS4525 `+`; middle → static → MS4525 `−`; forward TE capped. Multi-hole **skin** array → isolated BMP581 bay only, positioned at **40–60 % of body length** where a side port reads closest to freestream. |
| L4 | SUN barbs: **6 mm ID** hose → COTS reducer → MS4525 3/32″ ID (~2.38 mm). |
| L5 | Hose/Qwiic/USB **routes are a layout aid**, not geometry. `build_routes()` names every run and the assembly render draws them, so each one is shown to have somewhere to go before the pod is closed; the long runs use the corridor at y≈5–15, clear between the battery on the seam and the boards on the wall. `build_routes()` polylines are drawn in the QC PNGs and cut nothing, so the validator does not pretend to test them against real solids — v2's L5 check compared imaginary routes with real geometry. If a route ever needs real clearance, cut it and say so here. |
| L6 | Service cluster **locked and on the aft face**, on `tail_panel.stl`: SparkFun **COM-08837** rocker (E-Switch R1966A, snap-in **19.6 × 13.0** for a 2.0–3.0 mm panel — plate is 3.0 mm, no rebate), SparkFun **CAB-15464** Micro-B (10.5 × 7.5 window, M3 ears at **17 mm**, through-holes + nuts, RTV under the flange), two **Ø8.2 mm** holes for 5 mm chrome ABS LED holders. Stacked in Z: rocker high, USB centre, LEDs low. Switch → Babysitter **SYSOFF** (JP12 + GND); red LED = VOUT; blue = `!CHG!`. |
| L7 | SparkFun Pro Micro has **no OEM mounting holes**. It sits in `pm_tray.stl`, screwed to M2.5 insert standoffs. The tray's retaining rim runs in **-Y**, away from the wall — the board drops in from the open mating face. A rim running +Y retains nothing and fouls the land it bolts against. |
| L8 | **Labyrinth drain** at the aft end of the flat bottom (the cavity low point), clear of the battery and the flange rails: cavity → floor-level slot in the aft end wall → channel → down through the skin. No straight path from freestream to interior. |

## Process for agents

1. Read this file and `pod_enclosure.md` before editing.
2. Prefer changing params over inventing a new section family.
3. **Never** rebuild the OML as a union of separate solids. If the shape must
   change, change `section_params` / the scale laws.
4. After regen: `validate_pod.py` green; unique preview PNG name per revision.
5. Do not call STLs print-ready unless validate exits 0.
6. **Printed artifacts (`.stl` / `.step`) are committed only once the parts
   have been printed and checked**, and only with the user's explicit
   agreement for that commit. A green validator is not sufficient: every
   printed revision so far passed validation and still had defects that only
   the physical part revealed. The QC PNGs are small and meaningful, so they
   travel with the code. **P11** flags any STL not built from the current
   `wing_pod_v3.py`, so artifacts lagging the script is visible rather than
   silent. Verify reproducibility before an artifact commit: a fresh run must
   reproduce the STLs byte-for-byte.
7. Enclosure-only work stays on `master` (no PR) unless the user asks otherwise.
