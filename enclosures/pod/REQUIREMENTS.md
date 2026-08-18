# Wing pod v2 — non-negotiable requirements

Source of truth for agents and humans editing `wing_pod_v2.py`.
After any geometry change: regenerate, then run `validate_pod.py` (must exit 0)
before calling an STL print-ready.

```bash
cd enclosures/pod && uv run --project .. python wing_pod_v2.py
cd enclosures/pod && uv run --project .. python validate_pod.py
```

## Envelope & aero

| ID | Requirement |
|----|-------------|
| A1 | Overall skinny pod ~**220 × 52 × 77 mm** (L×W×H); asymmetric about seam (thin left, wider right for Babysitter 33 mm). |
| A2 | Midsection cross-section is the **same construction ellipse** the ogive fairings end on — not a separate rectangle. |
| A3 | Nose/tail are **multi-station ogive lofts**. Nose mouth fairs into the SUN tip OD with a **print-durable lip** (`NOSE_LIP_WALL` ≥ 1.5 mm radial — 0.7 mm PETG tore on the left half). Stern is a **convex rounded** cap (no pin/nipple). Nose tip centre near seam (`NOSE_TIP_YC`; exact 0.0 breaks OCCT fuse). |
| A4 | Fairings **hold full mid section** a few mm into the midspan so the junction is a volume overlap. |
| A5 | **No planar freestream-facing butt** at nose/tail↔mid junctions (no rectangular step into the airflow). |
| A6 | **Flat top** for wing-fairing mate: real chord width (not a vanishing apex cut); top edge may be square. |
| A7 | **Flat bottom** so the pod stands upright; bottom↔side edge has aero **radius** (`BOTTOM_EDGE_R`). |
| A8 | Left hollow must receive a real nose tip (xmin near 0), not only midsection. |
| A9 | Tail tip stays **fused** to the half (xmax ≈ `OUTER_L`); no dropped tip scrap / flat nub. |

## Shell seal

| ID | Requirement |
|----|-------------|
| S1 | Closed L+R cavity has **no freestream openings** except: (1) Prandtl/SUN tip mouth, (2) multi-hole static array (into the isolated BMP bay), (3) CAB-15464 panel USB, (4) COM-08837 rocker, (5) power LED holder, (6) charge LED holder. No Pro Micro exterior USB window. **No exterior battery door/slot** — pack is installed from the open mating face before close-up. Bottom side fairings must be continuous (parallel-offset R, not a through-slot). |
| S2 | Mating faces seal with a **thin rubber strip / O-cord in the right-half groove** and/or a thin RTV coat on the flange. Battery straddles the seam inside the sealed volume. |
| S3 | Static-port holes open **only into an isolated BMP(+mag) plenum**. That bay is sealed from the electronics cavity by printed walls + a serviceable `static_cover.stl` (foam/RTV gasket; Qwiic gland). Not an inaccessible cage. |

## Interior must stay interior

| ID | Requirement |
|----|-------------|
| I1 | Mating flange, pitot bulkheads, electronics land, **insert standoffs**, and static-bay walls are **intersected with the outer envelope** before union. No locating nubs as a board mount. |
| I2 | Built halves must not protrude outside `full_body_solid(0)` (extra volume ≈ 0). |
| I3 | Board mounts / static holes / panel cluster (CAB-15464 USB, COM-08837 rocker, LED holders) exist **only on `pod_right`**. Left is cover + cradle. Clamshell: **heat-set inserts in the right flange**; clearance + counterbore through the left cover. |
| I4 | Mating face stays **open for install** (battery + boards from the seam before close-up per S1). Flange is a **perimeter rail + local screw bosses** with insert/clearance holes — not a solid bulkhead across the bay. |
| I5 | Boards **wall-mount** on the right +Y inner skin (XZ plane) on **raised standoffs** (PCB not flush on the land). Standoff/insert axes are **±Y** so heat-set irons enter from the open mating face — no floor posts in wells. |
| I6 | Each board **3D keepout** (PCB + connectors/hose/Qwiic) must stay inside the cavity: no hit on outer skin, inner wall (except the mounting land), cradle/clamp, battery, or another board. `BOARD_GAP` between neighbors. Validator must use keepout boxes, not footprints alone. |
| I7 | Board insert pilots are reachable from y≈0: a clear **iron corridor** along −Y→+Y to each pilot. The static bay may have a **tool window** in its −Y wall (closed later by `static_cover.stl`) so BMP/mag inserts stay reachable — no inaccessible sealed box. |
| I8 | **Every board** is mounted with **M2.5 screws into heat-set inserts in raised standoffs** (`STANDOFF_H` ≥ 4 mm) so underside parts/Qwiic clear the land. Each board has ≥2 insert positions (`holes` or `clamp_posts`). Validator **must fail** empty `holes=[]` without clamp posts — that skip is how wall-mount dropped Pro Micro inserts. |

## Print / export (AnkerMake M5C)

| ID | Requirement |
|----|-------------|
| P1 | Each of `pod_left.stl`, `pod_right.stl` is a **single solid**. |
| P2 | Each STL is **watertight** (zero boundary edges after tessellation). |
| P3 | Tessellation tight enough for P2 (`tol≤0.03`, `ang≤0.05` as of 2026-08). Looser settings have produced open meshes AnkerMake rejects. |
| P4 | Body halves export **flange/mating-face down**, curved outer up, **rotated 45°** for bed diagonal; print AABB ≤ 210 mm (220−10 margin). |
| P5 | SUN-B tip protrudes one tip-length past pod nose; cradle clears upward barbs and locates aft recess boss. Aft pin **must not** bottom in the cup before the Ø10.65 shoulder seats and the aft face meets the aft bulkhead (`SUN_RECESS_BOSS_LEN` ≤ recess depth − 2.5 mm after print-1: 0.5 mm extra was ~2 mm too long). |
| P6 | **All printed-joint screws** (clamshell, board/clamp/tray, static cover) are **M2.5 into heat-set inserts** with **screw relief deeper than the insert** (hub-case: long screws pass the brass into PETG). No self-tap into PETG. LED holders use their own jam nuts. CAB-15464 USB ears are **M3 through-holes Ø3.3 + nuts inside** (included 14 mm screws are too long for a blind heat-set; trim or swap to M3×8). Rocker is snap-in. |
| P7 | Left-half nose lip survives print/handling (`NOSE_LIP_WALL` ≥ 1.5 mm). |
| P8 | `static_cover.stl` is a **single watertight solid** (closes the static-bay window). |
| P9 | `pm_tray.stl` is a **single watertight solid** (Pro Micro clamp tray). |

Slicer settings (supports, PETG starting points) live in
[`PRINT_RECIPE.md`](PRINT_RECIPE.md) — not in the STLs.

## Layout & pneumatics (do not silently change)

| ID | Requirement |
|----|-------------|
| L1 | Battery pocket **50 × 6 × 70 mm** (X×Y×Z) + **1 mm/side**; Y thin across seam; Z = height. Pocket notches interior/flange only — never pierces outer skin. |
| L2 | Pitot: **ESA SUN-B** — tip shoulder + aft recess boss + integral knurled-band clamp in L/R (no separate saddle / tubes-in-tube). Caliper ODs are source; **printed bores add FDM allowance** (print-1: 0.20 mm radial slip was OK on the aft barrel; same allowance was tight on tip/smooth; 0.08 mm clamp was too small). Barrel at ~x=95 is the fit reference. |
| L3 | SUN aft barb → pitot → MS4525 `+`; middle → static → MS4525 `−`; forward TE capped; multi-hole **skin** → isolated BMP581 bay (not the whole pod interior). Bay is serviceable: heat-set through the −Y window, then fit `static_cover.stl`. |
| L4 | SUN barbs: **6 mm ID** hose → COTS reducer → MS4525 3/32″ ID (~2.38 mm); v1 barbs tip Ø2.1 / shoulder Ø3.5. |
| L5 | Hose + Qwiic + USB-pigtail **routes** are named polylines in CAD; QC PNG draws them; validator rejects routes that pierce cradle/battery/boards or leave the cavity. |
| L6 | Panel COTS **locked**: SparkFun **COM-08837** rocker (E-Switch R1966A, snap-in **19.6 × 13.0** mm for 2.0–3.0 mm panel), SparkFun **CAB-15464** Micro-B 6″ (**17 mm** M3 ears, through-holes + nuts, RTV under flange), two **Ø8.2 mm** holes for 5 mm chrome ABS LED holders. Cuts sit on a **2.5 mm planar +Y pad** (ellipse is too curved for snap-in). Switch → Babysitter **SYSOFF** (JP12 + GND); red LED = VOUT; blue LED = `!CHG!`. |
| L7 | SparkFun Pro Micro has **no OEM mounting holes**. It sits in `pm_tray.stl`, screwed to ≥4 M2.5 insert standoffs (Z-overhangs — Baby is 1 mm in −X). |

## Process for agents

1. Read this file and `pod_enclosure.md` before editing.
2. Prefer changing params over inventing a new midsection family.
3. If mid/fairing section family must change, re-run junction checks (A2, A4, A5) before export.
4. After regen: `validate_pod.py` green; unique preview PNG name per shape revision (do not overwrite a stale `pod_v2_shape.png`).
5. Do not call STLs print-ready unless validate exits 0.
6. Enclosure-only work stays on `master` (no PR) unless the user asks otherwise.
