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
| A3 | Nose/tail are **multi-station ogive lofts** (rounded), tip centres near the seam (`TIP_YC`; exact 0.0 breaks OCCT fuse). |
| A4 | Fairings **hold full mid section** a few mm into the midspan so the junction is a volume overlap. |
| A5 | **No planar freestream-facing butt** at nose/tail↔mid junctions (no rectangular step into the airflow). |
| A6 | **Flat top** for wing-fairing mate: real chord width (not a vanishing apex cut); top edge may be square. |
| A7 | **Flat bottom** so the pod stands upright; bottom↔side edge has aero **radius** (`BOTTOM_EDGE_R`). |
| A8 | Left hollow must receive a real nose tip (xmin near 0), not only midsection. |

## Interior must stay interior

| ID | Requirement |
|----|-------------|
| I1 | Mating flange, pitot bulkheads, electronics deck, board posts, and nubs are **intersected with the outer envelope** before union. |
| I2 | Built halves must not protrude outside `full_body_solid(0)` (extra volume ≈ 0). |
| I3 | Board mounts / static bay / USB cutout exist **only on `pod_right`**. Left is cover + cradle + flange inserts. |

## Print / export (AnkerMake M5C)

| ID | Requirement |
|----|-------------|
| P1 | Each of `pod_left.stl`, `pod_right.stl` is a **single solid**. |
| P2 | Each STL is **watertight** (zero boundary edges after tessellation). |
| P3 | Tessellation tight enough for P2 (`tol≤0.03`, `ang≤0.05` as of 2026-08). Looser settings have produced open meshes AnkerMake rejects. |
| P4 | Body halves export **flange/mating-face down**, curved outer up, **rotated 45°** for bed diagonal; print AABB ≤ 210 mm (220−10 margin). |
| P5 | SUN-B tip protrudes one tip-length past pod nose; cradle clears upward barbs and locates aft recess boss. |
| P6 | No self-tap board pilots — **M2.5 heat-set** inserts + screw relief (hub-case scheme). |

Slicer settings (supports, PETG starting points) live in
[`PRINT_RECIPE.md`](PRINT_RECIPE.md) — not in the STLs.

## Layout & pneumatics (do not silently change)

| ID | Requirement |
|----|-------------|
| L1 | Battery pocket **50 × 6 × 70 mm** (X×Y×Z) + **1 mm/side**; Y thin across seam; Z = height. |
| L2 | Pitot: **ESA SUN-B** clamp (dims in `SUN_B_CALIPERS.md`); tip protrudes; no tubes-in-tube / printed plug. |
| L3 | SUN aft barb → pitot → MS4525 `+`; middle → static → MS4525 `−`; forward TE capped; multi-hole bay → BMP581 only. |
| L4 | SUN barbs: **6 mm ID** hose → COTS reducer → MS4525 3/32″ ID (~2.38 mm); v1 barbs tip Ø2.1 / shoulder Ø3.5. |

## Process for agents

1. Read this file and `pod_enclosure.md` before editing.
2. Prefer changing params over inventing a new midsection family.
3. If mid/fairing section family must change, re-run junction checks (A2, A4, A5) before export.
4. After regen: `validate_pod.py` green; unique preview PNG name per shape revision (do not overwrite a stale `pod_v2_shape.png`).
5. Do not call STLs print-ready unless validate exits 0.
6. Enclosure-only work stays on `master` (no PR) unless the user asks otherwise.
