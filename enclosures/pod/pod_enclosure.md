# `wing_pod_v2.py` — wing air-data pod (clamshell)

Parametric CadQuery model of the under-wing air-data pod. **v2** replaces the
v1 rectangular insert (`wing_pod_case.py` / `KingfisherPod.zip`) with a
left/right aerodynamic clamshell that *is* the outer shell.

**Non-negotiables:** see [`REQUIREMENTS.md`](REQUIREMENTS.md). After every
geometry change, regenerate and run the validator (must exit 0):

```bash
cd enclosures/pod && uv run --project .. python wing_pod_v2.py
cd enclosures/pod && uv run --project .. python validate_pod.py
```

- Printer/material: AnkerMake M5C, PETG. Slice recipe (supports, PETG starting
  points): [`PRINT_RECIPE.md`](PRINT_RECIPE.md). Exported STLs are already
  **flange / mating-face down**, curved outer up, and **rotated 45°** for the
  bed diagonal (10 mm margin vs 220 mm — script asserts the print AABB).
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
cd enclosures/pod && uv run --project .. python wing_pod_v2.py   # also runs validate
cd enclosures/pod && uv run --project .. python validate_pod.py  # or re-check alone
# STL-only (fast):  uv run --project .. python validate_pod.py --stl-only
```

Exports (CWD):

| File | Role |
|------|------|
| `pod_left.stl` / `.step` | Left half, print-oriented |
| `pod_right.stl` / `.step` | Right half (electronics), print-oriented |
| `pod_assembly.step` | Assembled (model orientation) + SUN-B placeholder |
| `pod_v2_*_*.png` | Shape previews — **unique name per revision** |
| `pod_v2_*_right_interior.png` | Ortho view into open `pod_right` (SUN + boards) |

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

1. Lofted nose fairing; **ESA SUN-B** cradle on the centerline (tip protrudes)
2. Constant midsection: MS4525 + boost beside cradle; battery slab; Babysitter
   + Pro Micro; BMP581 multi-hole bay; mag (short axis along X)
3. Lofted tail fairing (empty taper)

## Pneumatics (decoupled)

| Line | Source | Destination |
|------|--------|-------------|
| Total | SUN-B **aft** barb (pitot) | 6 mm hose → COTS reducer → MS4525 `+` |
| Static (airspeed) | SUN-B **middle** barb | 6 mm hose → COTS reducer → MS4525 `−` |
| TE | SUN-B **forward** barb | Capped / unused (Prandtl) |
| Static (baro) | Pod multi-hole side bay | BMP581 only |

Mount SUN with **barbs up** (ESA water tip). Hose escapes into the right half
toward the MS4525.

### Tubing sizes

- SUN-B barbs: **6 mm ID** silicone (stem OD ≈ 5.96 mm).
- TE/Holybro MS4525DO: **1/8″ barbed ports mate with 3/32″ ID tubing**
  (~2.38 mm ID). v1 calipers: tip Ø**2.1**, shoulder Ø**3.5**, spacing **4.3** mm.
- Step 6 mm → MS size with a **COTS reducer** (short hop of tiny tubing onto
  the sensor). Do not rely on nested hose alone.

## Pitot mount (ESA SUN-B)

Calipers: [`SUN_B_CALIPERS.md`](SUN_B_CALIPERS.md). Printed L/R cradle only
(no tubes-in-tube, no RTV plug):

- Outer nose **fairs into tip Ø8.93** (thin sharp lip at the mouth); tip-only
  bore through the nose bulkhead; Ø10.65 shoulder seats on that bulkhead’s
  **aft face** at `x=SHOULDER_BH_T` (forward stop)
- **Integral split clamp** on the knurled band (thick L/R land, snug bore;
  flange screws supply the press — no separate saddle STL)
- Aft blind recess (Ø6.03 × 7.06) seats on a printed locating boss
- Upward barb bay; hose escapes into the right half
- Primary flight load is the Prandtl cantilever into the brass SUN

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
