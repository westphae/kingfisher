# Wing pod v2 — AnkerMake print recipe

Slicer settings for `pod_left.stl` and `pod_right.stl` on an **AnkerMake M5C**
in **PETG**. STLs do not carry support or slice settings — use this recipe in
AnkerMake Studio (or PrusaSlicer with an M5C profile).

Geometry / export contracts: [`REQUIREMENTS.md`](REQUIREMENTS.md). Layout:
[`pod_enclosure.md`](pod_enclosure.md). SUN-B fit: [`SUN_B_CALIPERS.md`](SUN_B_CALIPERS.md).

## Before slicing

1. Regenerate + validate (must exit 0):

   ```bash
   cd enclosures/pod && uv run --project .. python wing_pod_v2.py
   cd enclosures/pod && uv run --project .. python validate_pod.py --stl-only
   ```

2. Import each STL as a **single solid**. If Studio complains about a corrupt /
   multi-body mesh, re-export; do not “fix” in the slicer.
3. **Do not rotate** body halves — orientation is baked into the STL (P4).
   Check the print AABB still fits the 220 mm bed with skirt/brim.

## Orientation (as-exported)

| File | On the bed | Notes |
|------|------------|--------|
| `pod_right.stl` | Mating flange down, curved outer up, **45°** diagonal | Electronics half |
| `pod_left.stl` | Same | Cover half |

Print one half at a time if the diagonal + skirt crowds the bed. The SUN-B
adapter is purchased metal — not printed.

## Supports

| Part | Supports | Why |
|------|----------|-----|
| `pod_left` / `pod_right` | **On** | Ogive outer, hollow shell, SUN cradle, deck/posts (right) leave overhangs above ~45° |

Suggested support settings (tune after first preview):

- Type: **tree / organic** if available (cleaner on the outer skin); else normal
- Overhang threshold: **45–50°**
- Top Z contact / interface: on (easier peel from PETG)
- Prefer **not** filling small horizontal holes (static array, Babysitter charge
  USB, insert
  pilots, cradle bore) if the slicer offers paint-on — clear those in preview

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
| Bed adhesion | Brim 5–8 mm (PETG + tall skinny footprint) |
| Cooling | PETG-moderate (avoid max fan on first layers) |

Temperatures: follow your PETG brand / Studio preset. Dry filament if the outer
skin looks fuzzy or stringy.

## After print

1. Remove supports carefully from the outer skin (cosmetic + aero face).
2. Deburr mating flange, SUN cradle bore / barb bay, charge USB, static holes,
   screw paths.
3. Install **M2.5 heat-set inserts** in the **right** flange + right board
   posts (no self-tap). Left cover is clearance only. Same pilot/depth scheme
   as the hub case.
4. Dry-fit L/R halves around the **SUN-B** (barbs up; tip protruding; aft recess
   on the locating boss) before final assembly. Plumb 6 mm hose → COTS reducer
   → MS4525; cap the forward TE barb.

## Checklist

- [ ] `validate_pod.py` green on the STLs you are about to load
- [ ] No slicer rotation on either half
- [ ] Supports on for both body halves; preview cleared of plugged holes
- [ ] Brim on; fits bed at 45°
- [ ] Inserts heat-set before first assembly torque
- [ ] SUN-B dry-fit + hose routing checked before flight hardware
