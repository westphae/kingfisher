# LiPo caliper sheet (fill in)

Measure the actual flight pack before the first `wing_pod_v3` print. **Measured 2026-08-18: 68.5 × 5.9 × 49.3 mm (X × Y × Z), leads on the aft
edge.** All three came in under the 70 × 6 × 50 spec figures the model had
assumed, and the aft lead exit is what the layout already wanted, so no
geometry had to move. Values are entered in `wing_pod_v3.py`.

**Why this sheet exists:** the pack's thickness across the flats is the single
measurement that sizes the whole pod. It sets `OUTER_H`, and because body halves
print flange-down at 45° (**P0**: `OUTER_L + OUTER_H ≤ 297`), every millimetre of
height also costs a millimetre of length — which comes straight out of the
boattail.

## How much each number matters

| Model param | Assumed | Spare | If it is wrong |
|---|---|---|---|
| `BATT_Z` (thickness across the flats) | 50.0 | **1.0 mm** | **Binding.** +1 mm → `OUTER_H` 62, max `OUTER_L` 235, fineness 3.71 → 3.68. +5 mm → `OUTER_H` 66, boattail 66 mm, fineness 3.51 |
| `BATT_X` (length) | 70.0 | 43.4 mm | Loose. Up to ~113 mm fits before the boattail floor rises past the pocket |
| `BATT_Y` (thickness in the seam direction) | 6.0 | 14.4 / 3.5 mm | Loose. 25.9 mm of clear span (y −7.5 → 18.4) if the pocket is re-centred |

Pocket clearances in the model: `BATT_CLR` = 1.0 mm/side in X and Y,
`BATT_CLR_Z` = 0.5 mm/side. `BATT_SEAL_KEEP` = 2.0 mm keeps the pocket from ever
reaching the flange sealing land.

## How to measure a soft pouch cell

A LiPo is not a machined part; the usual caliper technique will read low.

1. Wipe the jaws, close them fully, press **ZERO**.
2. **Do not clamp.** Close the jaws until they *just kiss* the pouch and stop.
   Squeezing a pouch cell easily reads 0.5–1 mm under, and on the one dimension
   that matters that error is a whole millimetre of pod.
3. **Measure at full charge.** A charged pouch is at its thickest, and pouches
   also swell with age — the fit has to survive an old, full cell, not a new,
   empty one.
4. **Measure over everything that goes in the pod**: heat-shrink, wrap, any
   foam tape already stuck on. Do not measure the bare cell.
5. **Thickness is not uniform.** Take it at the centre of the face and again
   near each sealed edge; **write down the largest**.
6. Take each critical number twice; if the two differ by more than 0.2 mm,
   measure a third time and keep the middle value.

---

## 1. Cell body

| ID | What to measure | mm |
|----|-----------------|----|
| C1 | **Length** — long edge, over the wrap, NOT including tabs/leads | |
| C2 | **Width** — short edge, over the wrap, NOT including tabs/leads | |
| C3 | **Thickness at the centre** of the large face | |
| C4 | **Thickness near the sealed edges** (largest of the four) | |
| C5 | State of charge when measured (full / storage / flat) | |
| C6 | Is there already foam tape or wrap included in C1–C4? (y/n) | |

## 2. The sealed lip and the tab end

The pouch edge where the tabs come out is usually wider and thicker than the
body, and it is what actually hits the wall.

| ID | What to measure | mm |
|----|-----------------|----|
| T1 | Which edge do the leads exit? (long edge / short edge / corner) | |
| T2 | How far the sealed lip stands proud of the body on that edge | |
| T3 | Thickness across the sealed lip itself | |
| T4 | How far the **wire** protrudes before it can bend (tab + strain relief) | |

## 3. Connector and wire

| ID | What to measure | mm / note |
|----|-----------------|-----------|
| K1 | Connector type (JST-PH 2.0, JST-XH, bare tabs, …) | |
| K2 | Connector body **length × width × height** | |
| K3 | Wire gauge / OD, and whether the pair is bonded or separate | |
| K4 | Free wire length from the pouch to the connector | |
| K5 | Is there a separate balance lead? (2S packs) | |

## 4. Orientation — the one that changes geometry

The pack sits at x 85…157, z 5…56, straddling the seam at y ±4. Its four edges
have very different room:

```
                    top edge: 2.5 mm to the inner skin
                 ┌───────────────────────────┐
  fwd edge:      │                           │   aft edge:
  2.5 mm to the  │        LiPo pack          │   43 mm of clear
  SUN bulkhead   │      x 85 … 157           │   cavity  <-- leads want to be here
                 └───────────────────────────┘
                 bottom edge: 2.5 mm to the inner skin
```

| ID | Question | Answer |
|----|----------|--------|
| O1 | With the pack laid in as above, which edge do the leads end up on? | |
| O2 | Can the pack be installed rotated 180° in X so the leads face aft? | |

**What I do with O1:**

- **Leads on the aft edge** — nothing changes; this is what the model assumes.
- **Leads on the forward edge** — I shift `BATT_X0` aft by ~20 mm. Free: there
  are 43.4 mm of spare X, and it costs no length, height or aero.
- **Leads on a top or bottom edge** — only 2.5 mm there, so either the pack
  moves in Z (only 1.0 mm available) or the leads route along the edge to the
  aft corner. Tell me the wire OD (K3) and I will check whether the pair lies
  flat in 2.5 mm, or shift the pack and re-run the validator.

## 5. Sanity check before you send it

- [ ] C3/C4 taken at full charge, jaws not compressed
- [ ] Largest thickness recorded, not the average
- [ ] Wrap/tape included
- [ ] If C4 > 51 mm the pod grows — I will re-run P0 and report the new fineness

---

## Status

- [x] Measured 2026-08-18: **X 68.5, Y 5.9, Z 49.3**, leads on the **aft** edge
- [x] `wing_pod_v3.py` params updated and `validate_pod.py` re-run green
- [ ] Re-measure at end of life if the pack is ever replaced — Z is the binding
      dimension and pouches swell. Current spare: **1.7 mm** on Z.
