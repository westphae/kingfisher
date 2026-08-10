# SUN-B caliper sheet (fill in)

Measure the **ESA SUN mounting adapter with end piece B** before we change the
pod cradle. Use mm. ±0.1 mm is plenty; ±0.2 mm is still usable.

**How to use a digital caliper (quick):**

1. Wipe jaws clean. Close them fully → press **ZERO** (on the outside jaws).
2. **Outside size** (OD, length): close the big lower jaws on the part; read the
  display. Don’t crush soft hose barbs — snug is enough.
3. **Inside size** (ID, hole): use the small upper jaws inside the bore/hole;
  expand until they touch both sides.
4. **Depth** (how deep a pocket/indent is): use the thin rod that slides out the
  end of the caliper; seat the caliper shoulder on the rim, rod to the bottom.
5. Take each critical number **twice**; if they differ by more than 0.2 mm,
  measure a third time and write the middle value.

**Photos (2026-08-09):** side full + side rear (barbs up, on ruler); aft face
(Photo B). Nose-end photo skipped (plain bore).

**Station map (nose tip = 0, from calipers — checks OK):**

```
0          24.75       52.25              77.52     85   100  114   124.03
|-----------|-----------|------------------|---------|----|----|-----|
 tip Ø8.93  | smooth    | thread Ø11.76    | barb    |TE  |stat|pitot| aft
            | Ø10.65    | (len ≈25.4)      | barrel  |    |    |     | face
```

Checks: L5+L6 = 24.75+27.50 = 52.25 = T3 ✓; T4−T3 = 25.27 ≈ T2 25.37 ✓;
B5−B4 = 15 = B7 ✓; B6−B5 = 14 = B8 ✓. Ruler photos agree within ~1 mm.

---

## 0. Identity


| Field                      | Value                               |
| -------------------------- | ----------------------------------- |
| Part (as marked / invoice) | SUN-B                               |
| Date measured              | August 9, 2026                      |
| Caliper brand / resolution | StewMac Caliper (0.01mm resolution) |


---

## 1. Overall body

Hold the adapter horizontal. **Nose** = where the Prandtl tube inserts.
**Aft** = end-piece B.

Two polished diameters ahead of the thread (your correction — needed).


| ID  | What to measure                                                    | How                          | mm     |
| --- | ------------------------------------------------------------------ | ---------------------------- | ------ |
| L1  | **Total length** nose tip → aft face                               | Outside jaws on the two ends | 124.03 |
| L2  | OD of **nose tip** (smaller polished step)                         | Outside jaws                 | 8.93   |
| L3  | OD of **larger polished barrel** (between tip step and thread)     | Outside jaws                 | 10.65  |
| L4  | **Inner diameter** at the nose (probe socket)                      | Inside jaws                  | 8.06   |
| L5  | **Axial length of tip** (nose → tip/large step)                    | Along side                   | 24.75  |
| L6  | **Axial length of large polished barrel** (step → thread start)    | Along side                   | 27.50  |
| L7  | OD of **matte barb-section barrel** (between barbs, not on a barb) | Outside jaws                 | 11.71  |


---

## 2. Threaded band


| ID  | What to measure                                   | How          | mm    |
| --- | ------------------------------------------------- | ------------ | ----- |
| T1  | Thread **major diameter** (crests)                | Outside jaws | 11.76 |
| T2  | Threaded section **length**                       | Along side   | 25.37 |
| T3  | Nose tip → **start of thread**                    | Along side   | 52.25 |
| T4  | Nose tip → **end of thread** (barb region begins) | Along side   | 77.52 |
| T5  | Thread count over a measured run | 28 threads / ~25 mm (re-confirmed) | see below |


**Pitch interpretation:** 28 threads / 25 mm ⇒ pitch 25/28 ≈ **0.893 mm** ⇒
**~28.4 TPI**. That is much closer to imperial **28 TPI** (pitch = 25.4/28 ≈
0.907 mm; ~27.6 threads per exact 25 mm) than to M12×1.0 or M12×0.75. If the
“25 mm” run was actually ~1″ on a ruler, the count is exactly 28 TPI.

Major Ø11.76 mm (0.463″) is **between** common 28‑TPI sizes (7/16‑28 UNEF ≈
11.11 mm, ½‑28 UNEF ≈ 12.7 mm) — likely a specialty OD with **28 TPI**, not a
bin-stock nut. Confirm by trial-fitting a 28 TPI nut/die or measuring one full
inch of thread before CAD of a captured nut.

---

## 3. Pneumatic barbs (6 mm hose fittings)

Barbs **up**. Numbered **nose → aft**:

1. **Forward** — TE (unused for Prandtl) @ 85 mm
2. **Middle** — static @ 100 mm
3. **Aft** — pitot / total @ 114 mm


| ID  | What to measure                             | How             | mm    |
| --- | ------------------------------------------- | --------------- | ----- |
| B1  | Barb **stem OD** (hose seat)                | Outside jaws    | 5.96  |
| B2  | Barb **tip** OD                             | Outside jaws    | 4.97  |
| B3  | Barrel **top → barb tip** (height above OD) | Outside / depth | 16.08 |
| B4  | Nose → center barb 1 (TE)                   | Along side      | 85.0  |
| B5  | Nose → center barb 2 (static)               |                 | 100.0 |
| B6  | Nose → center barb 3 (pitot)                |                 | 114.0 |
| B7  | Spacing barb1→barb2                         | check = B5−B4   | 15.0  |
| B8  | Spacing barb2→barb3                         | check = B6−B5   | 14.0  |


Tip height above axis ≈ B3 + (L7)/2 once L7 is filled.

---

## 4. End piece B (aft mount) — clarified

Photo B shows a **circular recess in the aft face** (looking along the axis).
ESA’s “hole for external mounting support” might be that **axial blind cup**,
or a separate **cross-drilled** hole through the side of the end piece. Your
E2 = E5 = 7.06 suggests both were read as the **same depth** — see questions
below.


| ID  | What to measure                                                                                                                                                                                                                       | How                                                    | mm / note          |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ | ------------------ |
| E1  | Diameter of the **central aft recess / blind hole** (the dark circle in Photo B)                                                                                                                                                      | Inside jaws in that recess                             | 6.03               |
| E2  | **Only if** there is a **sideways (radial) pin hole** through the end piece: distance from aft face → that hole’s centerline. If there is **no** radial hole, write `n/a` (do not re-use depth).                                      | Along side to radial hole axis                         | n/a                |
| E3  | Axial recess: through into the probe bore, or blind?                                                                                                                                                                                  | through / blind                                        | blind              |
| E4  | Diameter of the **recessed circle** in Photo B (the step you see), **not** the outside of the whole adapter. If 11.73 was the **outer rim OD** of the aft face, move that to L7/E6 and re-measure the inner recess OD (may match E1). | Inside jaws across the recess, or OD of the inner step | 6.03               |
| E5  | How deep the aft recess goes in from the aft face                                                                                                                                                                                     | Depth rod, shoulder on aft rim                         | 7.06               |
| E6  | Aft body OD (outside of the ring in Photo B) and any flats/hex                                                                                                                                                                        | Outside jaws on aft barrel                             | round; OD → see L7 |


**E2 meant:** “Where along the tube is a **transverse** mounting pin hole?” — only
relevant if a drill goes in from the **side**. It is **not** “how deep is the
cup.”

**E4 meant:** Looking at Photo B, the diameter of the **inner recessed disk**
(cup), i.e. the hole/pocket we can print a locating boss into — usually close
to E1 if the cup is a simple blind bore. If the cup has a wide counterbore plus
a smaller hole at the bottom, note both.

---

## 5. Fit intent


| ID  | Question                                                           | Answer                                                          |
| --- | ------------------------------------------------------------------ | --------------------------------------------------------------- |
| F1  | How far should the **SUN nose** stick out of the **pod nose tip**? | **24.75 mm** (= L5): tip protrudes; Ø10.65 step at pod nose tip |
| F2  | Hose for SUN barbs                                                 | 6 mm ID silicone                                                |
| F3  | Step-down to MS4525                                                | COTS reducer                                                    |


---

## 6. Comments / oddities

Yellowish ring on the matte barrel between aftmost and middle barb (visible in
photos) — looks like sealant at an end-piece joint; ignore for cradle OD.

---

## Status

- [x] L7, E2=`n/a`, E4=E1 filled (2026-08-09)
- [x] Params consumed by `wing_pod_v2.py` SUN-B cradle