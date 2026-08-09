# `wing_pod_v2.py` — wing air-data pod (clamshell)

Parametric CadQuery model of the under-wing air-data pod. **v2** replaces the
v1 rectangular insert (`wing_pod_case.py` / `KingfisherPod.zip`) with a
left/right aerodynamic clamshell that *is* the outer shell.

- Printer/material: AnkerMake M5C, PETG. Exported STLs are already **flange /
  mating-face down**, curved outer up, and **rotated 45°** for the bed
  diagonal (10 mm margin vs 220 mm — script asserts the print AABB).
- Shape: **skinny tall ellipse** (~220 × 52 × 77 mm) chopped to a **flat top**
  (wing-fairing mate, square edge) and **flat bottom** (stands upright) with a
  **radiused bottom edge** for aero; **rounded ogive nose and tail**
  (multi-station loft, tip centres near the seam). Midsection uses the *same*
  ellipse as the fairing end-stations (held through a short overlap) so there
  is no rectangular freestream-facing step at the junction. Section is
  asymmetric about the seam (thin left cover, wider right electronics bay).
- Asymmetry: **board mounts / static bay / USB are on `pod_right` only**;
  `pod_left` is the cover (pitot cradle + flange inserts).
- Fasteners: M2.5 brass heat-set inserts (same pilot / depth / screw-relief
  scheme as the hub case). Left half = inserts; right half = clearance +
  counterbore. Board posts also take inserts (not self-tap pilots).
- Internal blanks (flange, bulkheads, deck, posts, static-bay walls) are
  **intersected with the outer envelope** so rectangular stock cannot poke
  through the curved skin.

## Regenerating

```bash
uv sync --all-packages   # from repo root
cd enclosures/pod && uv run --project .. python wing_pod_v2.py
```

Exports (CWD):

| File | Role |
|------|------|
| `pod_left.stl` / `.step` | Left half, print-oriented |
| `pod_right.stl` / `.step` | Right half (electronics), print-oriented |
| `pitot_plug.stl` / `.step` | Aft plug (STL = flange on bed, stem up; STEP = model pose) |
| `pod_assembly.step` | Assembled (model orientation) |
| `pod_v2_*_*.png` | Shape previews — **unique name per revision** |

## Coordinate system & layout

Assembled frame: origin at outer nose tip on the seam / bottom. **+X aft**,
**+Y right**, **+Z up** (flat top at `OUTER_H`). Split plane is **Y = 0**.

Print STLs (`pod_left.stl` / `pod_right.stl`): drop on the bed as-exported —
no slicer rotation needed. Each STL is a **single solid** (AnkerMake rejects
multi-body compounds). STEP files stay in model orientation for Fusion.

**Left vs right:** only `pod_right` has the electronics deck, M2.5 insert
posts, static bay, and USB cutout. `pod_left` is the cover (shell + pitot
cradle bulkheads + flange inserts). A left-only print will not show sensor
mounts — that is intentional, not a fit-check stub.

Nose → aft:

1. Lofted nose fairing; pitot cradle (outer tube, ~100 mm) on the centerline
2. Constant midsection: MS4525 + boost beside cradle; battery slab; Babysitter
   + Pro Micro; BMP581 multi-hole bay; mag (short axis along X)
3. Lofted tail fairing (empty taper)

## Pneumatics (decoupled)

| Line | Source | Destination |
|------|--------|-------------|
| Total | Prandtl 4 mm fitting via plug | MS4525 `+` (barb tubing) |
| Static (airspeed) | Prandtl static via plug | MS4525 `−` |
| Static (baro) | Pod multi-hole side bay | BMP581 only |
| Optional | Plug `static2` port | Tee test into BMP581 bay |

No protruding nipples — probe lines enter through the printed plug; sensor
side uses through-bores / short jumpers onto MS4525 barbs.

### MS4525 tubing size

TE/Holybro MS4525DO: **1/8″ barbed ports mate with 3/32″ ID tubing**
(~2.38 mm ID). v1 calipers on the Holybro carrier: tip Ø**2.1**, shoulder
Ø**3.5**, barb spacing **4.3** mm. The plug steps **4.0 mm** probe-side bore
→ **~3.5 mm OD** sensor-side line (tune `PROBE_BORE` / `MS_BORE`).

## Pitot mount (SUN replacement)

Tubes-in-tube (purchased metal + printed plug):

- Outer tube: ID ≈ 10.2, OD ≈ 12 (VERIFY stock), length 100 mm — clamped in
  the printed L/R cradle
- Three inner tubes: OD 10 / ID 8, with thick O-rings (T ≳ 2 mm, ID ≲ 8 mm)
  between segments sealing on the Prandtl 8 mm shaft
- Printed aft plug: RTV into the outer tube; flange seats at the cradle aft
  face; ports for total, static, and optional static2

Primary flight load is the ~60 cm Prandtl cantilever — do not skip the metal
outer tube.

## Battery

Pocket sized for **50 × 6 × 70 mm** (X × Y × Z) plus **1 mm** clearance per
side; wedge with double-sided foam tape. Thickness is across the seam
(centerline slab).

## Fairing / wing attach (deferred)

Flat top deck is the mate for a *separate* printed fairing that blends to
wing curvature and bolts to an inspection plate. Pod↔fairing latch (L-pins
etc.) and sailplane sealing tape are follow-ons — not in this script yet.

## U.FL antenna

Grommet / exit path deferred until an ESP32 module with U.FL is in use.
PCB antenna is fine for early RF testing.

## v1 archive

`../KingfisherPod.zip` and `wing_pod_case.py` are the old insert-style tray
(base + lid, socket stubs, sealed bay shared with MS4525). Do not evolve v1;
tune v2 params instead.
