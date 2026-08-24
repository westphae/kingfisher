# Wing pod v3 — AnkerMake print recipe

Slicer settings for `pod_left.stl`, `pod_right.stl`, `tail_panel.stl`,
`static_bay.stl`, `pm_tray.stl`, and `sun_clamp.stl` on an **AnkerMake M5C** in **PETG**. STLs do not carry support or
slice settings — use this recipe in AnkerMake Studio (or PrusaSlicer with an
M5C profile).

Geometry / export contracts: [`REQUIREMENTS.md`](REQUIREMENTS.md). Layout:
[`pod_enclosure.md`](pod_enclosure.md). SUN-B fit: [`SUN_B_CALIPERS.md`](SUN_B_CALIPERS.md).

## Before slicing

1. Regenerate + validate (must exit 0):

   ```bash
   cd enclosures/pod && uv run --project .. python wing_pod_v3.py
   cd enclosures/pod && uv run --project .. python validate_pod.py --stl-only
   ```

2. Import each STL as a **single solid**. If Studio complains about a corrupt /
   multi-body mesh, re-export; do not “fix” in the slicer.
3. **Do not rotate or move anything** — orientation *and bed position* are baked
   into every STL. All five parts are exported already **centred on the bed**
   (110, 110) and sitting on z=0. If your slicer offers "load at model
   position", use it; if it auto-centres, that is the same result.
   A part parked on the origin corner slices with its skirt/brim at negative
   X/Y and the job is rejected with *"a toolpath outside the print area"*.
4. **Bed-edge budget.** Body halves leave **11.2 mm** from the part edge to the
   bed edge (X binds on `pod_right`, Y on `pod_left`). Everything drawn outside
   the object shares that margin:

   ```
   brim width + skirt distance + (skirt loops x ~0.45) <= 11.2 mm
   ```

   Skirt distance is measured **from the brim**, not the object. The stock
   6 mm skirt distance on top of a 5 mm brim is 11.4 mm and the job is rejected
   with *"a toolpath outside the print area"*.

   | Brim | Skirt | Loops | Outermost | |
   |------|-------|-------|-----------|--|
   | 5 | off | - | 5.0 | **use this** |
   | 5 | 3 | 1 | 8.4 | ok, keeps a flow check |
   | 5 | 6 | 1 | 11.4 | rejected |
   | 8 | 6 | 1 | 14.4 | rejected |

   The M5C draws its own prime line, so the skirt is not needed for priming.
   Do **not** drop the skirt below ~2 mm: printed flange-down, the first layer
   *is* the mating face, and a skirt that fuses to it leaves a ridge on the
   sealing surface (S2).

## Orientation (as-exported)

| File | On the bed | Notes |
|------|------------|--------|
| `pod_plate.stl` | **Outer skin DOWN**, cavity up, **45°** diagonal | Board plate — the part you reprint |
| `pod_bowl.stl` | Same | Deep half: SUN cradle, battery, aft rim |

**Outer skin down, not flange down.** Flange-down puts the cavity ceiling and
every inward-hanging feature over open air; print-3 came out packed with
support throughout the inside. Measured on the real halves at a 45° threshold:

| part | flange-down | outside-down |
|---|---|---|
| plate | 11782 mm² support, 2096 bed | **8399** (−29 %), **5288** bed |
| bowl | 12337 mm² support, 2610 bed | **8666** (−30 %), **6037** bed |

The flank is flat over the whole midbody, so the first layer is a large flat
face instead of a thin flange ring. Two consequences worth knowing:

- The **mating face is now the top surface**. Give it enough top layers to come
  out flat, and check it with a straightedge before trusting the gasket (S2).
- The **aero skin takes the bed finish**, and the brim attaches to its
  perimeter. Print on a smooth sheet if you want a smooth outer surface, and
  expect to dress the brim witness line near the parting line.
| `tail_panel.stl` | Large face down | Aft service plate — the face with the counterbores goes **up** |
| `static_bay.stl` | **Closed face down** | BMP plenum cup; tabs overhang slightly at the top |
| `pm_tray.stl` | Large face down | Pro Micro clamp tray |
| `sun_clamp.stl` | **Split face down** | Cap that closes the SUN saddle; the bore half-cylinder faces up |

Print one half at a time if the diagonal + skirt crowds the bed. The SUN-B
adapter is purchased metal — not printed.

## Supports

| Part | Supports | Why |
|------|----------|-----|
| `pod_left` / `pod_right` | **On** | Ogive outer, hollow shell, SUN cradle, standoffs (right) leave overhangs above ~45° |
| `tail_panel` | **Off** | Flat plate |
| `static_bay` | **Off** | Shallow cup, tabs are a short overhang |
| `pm_tray` | **Off** | Flat tray |
| `sun_clamp` | **Off** | Half-bore in the up face, so it self-supports |

Suggested support settings (tune after first preview):

- Type: **tree / organic** if available (cleaner on the outer skin); else normal
- Overhang threshold: **45–50°**
- Top Z contact / interface: on (easier peel from PETG)
- Prefer **not** filling small holes (static array, insert pilots, cradle bore,
  and especially the **labyrinth drain** — its channel is a 2 mm internal
  passage) if the slicer offers paint-on; clear those in preview

After a good slice, **File → Save Project** as a `.3mf` next to the STLs if you
want a reusable Studio snapshot (mesh + these settings). Plain mesh-only 3MF
export usually drops support/config.

## Material & quality (starting point)

Use the stock **AnkerMake M5C + PETG** profile, then:

| Setting | Suggested |
|---------|-----------|
| Nozzle | 0.4 mm |
| Layer height | 0.20 mm (0.16 mm if outer fairing finish matters more than time) |
| Walls / perimeters | ≥ 3 |
| Top / bottom layers | ≥ 4 |
| Infill | 15–25% gyroid or grid |
| Bed adhesion | Brim **5 mm**, skirt **off** (see step 4 — 11.2 mm to the bed edge) |
| Cooling | PETG-moderate (avoid max fan on first layers) |

Temperatures: follow your PETG brand / Studio preset. Dry filament if the outer
skin looks fuzzy or stringy.

## After print

1. Remove supports carefully from the outer skin (cosmetic + aero face).
2. Deburr mating flange, SUN cradle bore / barb bay, panel USB/rocker/LED holes,
   static holes, screw paths.
3. Install **M2.5 heat-set inserts** in the **right** flange + **every board
   standoff** (including Pro Micro clamp posts) + the four static-bay land inserts + the
   four **aft-panel** bosses (no self-tap, no nubs). Flange and board inserts go
   in from the **open mating face** (iron along Y) before installing PCBs; BMP
   through the static-bay window; aft-panel inserts from **outside the base**
   (iron along X). Left cover is clearance only. Same pilot/depth/screw-relief
   scheme as the hub case. Seat the Pro Micro in `pm_tray.stl` and screw the
   tray down, mount the BMP581 and heat-set its inserts on the OPEN wall, then screw `static_bay.stl` over it on a foam/RTV gasket.
4. **Populate `tail_panel.stl` on the bench, before it goes on the pod** — this
   is the whole point of it being a separate plate. Snap in the COM-08837
   rocker (SYSOFF → JP12 / GND; leave onboard S1 OFF). RTV the CAB-15464 flange
   and fasten with M3 + nuts (trim the included 14 mm screws or use M3×8). Press
   the 5 mm LEDs into their chrome holders and fit the jam nuts: red = VOUT (pod
   powered), blue = `!CHG!`. Leave a service loop on the harness — the plate has
   to come away from the base to be worked on.
5. Dry-fit L/R halves around the **SUN-B** (barbs up; **45 mm** protruding; the
   Ø10.65→Ø11.76 step seated on the nose bulkhead; aft recess on the locating
   boss) before final assembly. Plumb 6 mm hose → COTS reducer
   → MS4525; cap the forward TE barb.

## Checklist

- [ ] `validate_pod.py` green on the STLs you are about to load
- [ ] No slicer rotation **or repositioning** on either half
- [ ] Brim 5 mm and skirt off (or skirt ≤ 3 mm); no toolpath crosses the bed boundary in preview
- [ ] Supports on for both body halves; preview cleared of plugged holes
- [ ] Labyrinth drain channel open in preview (2 mm internal passage)
- [ ] `tail_panel` populated on the bench before fitting to the base
- [ ] Brim on; fits bed at 45°
- [ ] Inserts heat-set before first assembly torque
- [ ] SUN-B dry-fit + hose routing checked before flight hardware
