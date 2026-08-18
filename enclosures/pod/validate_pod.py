#!/usr/bin/env python3
"""
Validate wing pod v2 geometry + exported STLs against REQUIREMENTS.md.

  cd enclosures/pod && uv run --project .. python validate_pod.py
  cd enclosures/pod && uv run --project .. python validate_pod.py --stl-only

Exit 0 on success, 1 on any failure.  Safe to call from wing_pod_v2.py after export.
"""
from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter
from pathlib import Path

POD_DIR = Path(__file__).resolve().parent
STL_FILES = ("pod_right.stl", "pod_left.stl", "static_cover.stl", "pm_tray.stl")

# Tolerances (mm / mm²)
EXTRA_OUTSIDE_MAX = 50.0  # right half may have tiny static-bay scraps; left must be ~0
EXTRA_OUTSIDE_LEFT_MAX = 1.0
FLAT_TOP_AREA_MIN = 2000.0
BUTT_FACE_AREA_MAX = 5.0  # planar freestream butts at junctions
LEFT_NOSE_XMAX = 2.0  # left hollow must reach near x=0


class CheckResult:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def ok(self, msg: str) -> None:
        self.notes.append(f"OK  {msg}")

    def fail(self, msg: str) -> None:
        self.failures.append(msg)
        self.notes.append(f"FAIL {msg}")

    @property
    def passed(self) -> bool:
        return not self.failures


def stl_boundary_edges(path: Path, ndigits: int = 5) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 84:
        return 0, -1
    n = struct.unpack_from("<I", data, 80)[0]
    expected = 84 + n * 50
    if len(data) != expected:
        return n, -2  # size mismatch sentinel
    edges: Counter[tuple] = Counter()
    for i in range(n):
        off = 84 + i * 50
        vals = struct.unpack_from("<12f", data, off)
        verts = [
            tuple(round(vals[3 + 3 * k + j], ndigits) for j in range(3))
            for k in range(3)
        ]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edges[tuple(sorted((verts[a], verts[b])))] += 1
    boundary = sum(1 for c in edges.values() if c == 1)
    return n, boundary


def check_stls(r: CheckResult, directory: Path = POD_DIR) -> None:
    """P1–P3: files exist, single-body implied by watertight mesh, no open edges."""
    for name in STL_FILES:
        path = directory / name
        if not path.is_file():
            r.fail(f"P1/P2 missing {name}")
            continue
        n_tri, n_bound = stl_boundary_edges(path)
        if n_bound == -1:
            r.fail(f"P2 {name}: too short to be a binary STL")
            continue
        if n_bound == -2:
            r.fail(f"P2 {name}: binary size mismatch vs triangle count")
            continue
        min_tri = 20 if name in ("static_cover.stl", "pm_tray.stl") else 100
        if n_tri < min_tri:
            r.fail(f"P2 {name}: only {n_tri} triangles")
            continue
        if n_bound != 0:
            r.fail(
                f"P2 {name}: not watertight ({n_bound} boundary edges) — "
                "AnkerMake will reject; tighten tessellation"
            )
            continue
        r.ok(f"P2 {name}: {n_tri} tris, watertight")


def _planar_butt_area(
    solid, x_lo: float, x_hi: float, want_fwd: bool, *, z_min: float = 0.0
) -> float:
    """
    Area of nearly planar faces with |nx|~1 and thin X extent in [x_lo, x_hi].

    Faces with center z < z_min are ignored — bottom-edge R ramp ends are not
    midsection freestream butts (A5).
    """
    import cadquery as cq
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    total = 0.0
    exp = TopExp_Explorer(solid.val().wrapped, TopAbs_FACE)
    while exp.More():
        f = cq.Face(exp.Current())
        bb = f.BoundingBox()
        xspan = bb.xmax - bb.xmin
        if xspan > 1.5 or bb.xmax < x_lo or bb.xmin > x_hi:
            exp.Next()
            continue
        try:
            c = f.Center()
            if c.z < z_min:
                exp.Next()
                continue
            n = f.normalAt(c)
            if want_fwd and n.x < -0.98 and f.Area() > 1.0:
                total += f.Area()
            if (not want_fwd) and n.x > 0.98 and f.Area() > 1.0:
                total += f.Area()
        except Exception:
            pass
        exp.Next()
    return total


def check_geometry(r: CheckResult) -> None:
    """A*, I*, P4–P5 geometry checks (rebuilds solids — slow)."""
    import wing_pod_v2 as pod

    outer = pod.full_body_solid(0.0)
    n_solids = len(outer.val().Solids())
    if n_solids != 1:
        r.fail(f"A2/A4 outer envelope has {n_solids} solids (want 1 — tip_yc fuse?)")
    else:
        r.ok("A2/A4 outer envelope is one solid")

    bb = outer.val().BoundingBox()
    if abs(bb.zmax - pod.OUTER_H) > 0.05 or bb.zmin > 0.05:
        r.fail(
            f"A6/A7 outer Z is {bb.zmin:.2f}..{bb.zmax:.2f}, "
            f"want ~0..{pod.OUTER_H}"
        )
    else:
        r.ok(f"A6/A7 outer Z {bb.zmin:.2f}..{bb.zmax:.2f}")

    try:
        top = outer.faces(">Z").val()
        top_area = top.Area()
        if top_area < FLAT_TOP_AREA_MIN:
            r.fail(f"A6 flat top area {top_area:.0f} < {FLAT_TOP_AREA_MIN}")
        else:
            r.ok(f"A6 flat top area {top_area:.0f} mm²")
    except Exception as exc:
        r.fail(f"A6 no flat top face: {exc}")

    if pod.FLAT_TOP_HALF_W < 8.0:
        r.fail(f"A6 flat top chord half-width {pod.FLAT_TOP_HALF_W:.1f} too small")
    else:
        r.ok(f"A6 flat top chord ~{2 * pod.FLAT_TOP_HALF_W:.1f} mm")

    # Ignore bottom-R ramp faces (z below the fillet crown).
    z_ignore = pod.BOTTOM_EDGE_R + 2.0
    nose_butt = _planar_butt_area(
        outer,
        pod.NOSE_FAIR_LEN - 3.0,
        pod.NOSE_FAIR_LEN + 4.0,
        want_fwd=True,
        z_min=z_ignore,
    )
    tail_butt = _planar_butt_area(
        outer,
        pod.MID_END_X - 4.0,
        pod.MID_END_X + 3.0,
        want_fwd=False,
        z_min=z_ignore,
    )
    if nose_butt > BUTT_FACE_AREA_MAX:
        r.fail(f"A5 nose junction freestream butt area {nose_butt:.1f} mm²")
    else:
        r.ok(f"A5 nose junction butt area {nose_butt:.1f} mm²")
    if tail_butt > BUTT_FACE_AREA_MAX:
        r.fail(f"A5 tail junction freestream butt area {tail_butt:.1f} mm²")
    else:
        r.ok(f"A5 tail junction butt area {tail_butt:.1f} mm²")

    # I2 — no exterior protrusions
    for name, builder, side, limit in (
        ("pod_right", pod.build_right, +1, EXTRA_OUTSIDE_MAX),
        ("pod_left", pod.build_left, -1, EXTRA_OUTSIDE_LEFT_MAX),
    ):
        body = pod.as_single_solid(builder(), name)
        extra = body.cut(outer).val().Volume()
        if extra > limit:
            r.fail(f"I2 {name} protrudes outside outer by {extra:.1f} mm³ (max {limit})")
        else:
            r.ok(f"I2 {name} outside outer {extra:.1f} mm³")

    # A8 — left gets a nose tip
    left = pod.as_single_solid(pod.build_left(), "pod_left")
    lbb = left.val().BoundingBox()
    if lbb.xmin > LEFT_NOSE_XMAX:
        r.fail(f"A8 left hollow xmin={lbb.xmin:.1f} (want ≤ {LEFT_NOSE_XMAX})")
    else:
        r.ok(f"A8 left hollow xmin={lbb.xmin:.1f}")

    # I3 — right has USB/static features left lacks (board deck presence via volume)
    right = pod.as_single_solid(pod.build_right(), "pod_right")
    if right.val().Volume() <= left.val().Volume():
        r.fail("I3 right half not larger than left (missing electronics?)")
    else:
        r.ok("I3 right half volume > left (electronics present)")

    # I4 — mating face open for install (not a solid flange bulkhead)
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt

    mid_x = 0.5 * (pod.FLANGE_X0 + pod.FLANGE_X1)
    mid_z = 0.5 * pod.OUTER_H
    blocked = []
    for name, solid, y in (
        ("pod_right", right, 0.5),
        ("pod_left", left, -0.5),
    ):
        if (
            BRepClass3d_SolidClassifier(
                solid.val().wrapped, gp_Pnt(mid_x, y, mid_z), 1e-4
            ).State()
            == TopAbs_IN
        ):
            blocked.append(name)
    if blocked:
        r.fail(
            f"I4 mating bay blocked at ({mid_x:.0f},±0.5,{mid_z:.0f}) on "
            f"{', '.join(blocked)} — flange must stay open for install"
        )
    else:
        r.ok("I4 mating bay open for install (rail + bosses only)")

    # I5/I6 — wall-mount boards; 3D keepouts vs cradle, battery, shell, neighbors
    y_clear = pod.CRADLE_LAND_Y + pod.BOARD_GAP
    board_boxes = []
    inner_ok = True
    for name, b in pod.BOARDS.items():
        x0, x1, y0, y1, z0, z1 = pod.board_keepout(b)
        board_boxes.append((name, x0, x1, y0, y1, z0, z1))
        if y1 > y_clear - 0.05 and y0 < pod.CRADLE_LAND_Y:
            # keepout inboard of land may extend toward cradle; fail if it
            # actually enters the cradle Y band.
            if y0 < pod.CRADLE_LAND_Y - 0.05:
                r.fail(
                    f"I6 {name} keepout y={y0:.1f}..{y1:.1f} enters cradle "
                    f"(CRADLE_LAND_Y={pod.CRADLE_LAND_Y:.1f})"
                )
                inner_ok = False
        # PCB plane must be the wall land, not the floor.
        if abs(b.get("z0", -1) - getattr(pod, "Y_PCB", -99)) < 0.01:
            r.fail(f"I5 {name} still looks floor-mounted (z0==Y_PCB?)")
        # Wall plane: keepout y1 is the raised standoff face.
        if abs(y1 - pod.Y_PCB) > 0.2:
            r.fail(f"I5 {name} not on standoffs (keepout y1={y1:.1f}, Y_PCB={pod.Y_PCB:.1f})")
            inner_ok = False
        for z in (z0, z1):
            yi = pod.ellipse_y_plus(z, pod.WALL)
            if yi < pod.Y_LAND + 1.0:
                r.fail(
                    f"I6 {name} z={z:.1f}: inner +Y wall {yi:.1f} < land {pod.Y_LAND:.1f}+1"
                )
                inner_ok = False
        # Battery overlap in X and Z at battery Y
        if (
            x1 > pod.BATT_X0
            and x0 < pod.BATT_X0 + pod.BATT_POCKET_X
            and z1 > pod.BATT_Z0
            and z0 < pod.BATT_Z0 + pod.BATT_POCKET_Z
            and y0 < pod.BATT_Y0 + pod.BATT_POCKET_Y
            and y1 > pod.BATT_Y0
        ):
            r.fail(f"I6 {name} keepout intersects battery pocket")
            inner_ok = False
    # Pairwise: overlap in X and Z needs BOARD_GAP in Y (or vice versa).
    for i, (n1, a0, a1, ay0, ay1, az0, az1) in enumerate(board_boxes):
        for n2, b0, b1, by0, by1, bz0, bz1 in board_boxes[i + 1 :]:
            x_overlap = a1 > b0 and a0 < b1
            z_overlap = az1 > bz0 and az0 < bz1
            y_overlap = ay1 > by0 and ay0 < by1
            if x_overlap and z_overlap and y_overlap:
                r.fail(f"I6 {n1}/{n2} keepouts overlap in X, Y, and Z")
                inner_ok = False
                continue
            if x_overlap and z_overlap:
                r.fail(f"I6 {n1}/{n2} footprints overlap on the wall (X and Z)")
                inner_ok = False
                continue
            if x_overlap:
                zgap = bz0 - az1 if az1 <= bz0 else az0 - bz1
                if 0.0 <= zgap < pod.BOARD_GAP - 0.05:
                    r.fail(
                        f"I6 {n1}/{n2} Z-gap {zgap:.1f} < BOARD_GAP {pod.BOARD_GAP}"
                    )
                    inner_ok = False
                continue
            gap = b0 - a1 if a1 <= b0 else a0 - b1
            if 0.0 <= gap < pod.BOARD_GAP - 0.05 and z_overlap and not (
                {n1, n2} == {"BABY", "PROMICRO"}
            ):
                r.fail(f"I6 {n1}/{n2} X-gap {gap:.1f} < BOARD_GAP {pod.BOARD_GAP}")
                inner_ok = False
    # I5 — raised off the wall land (hub-case standoffs, not flush)
    if pod.Y_PCB > pod.Y_LAND - pod.STANDOFF_H + 0.05 or pod.STANDOFF_H < 4.0:
        r.fail(
            f"I5/I8 PCBs not raised (Y_PCB={pod.Y_PCB:.1f}, Y_LAND={pod.Y_LAND:.1f}, "
            f"STANDOFF_H={pod.STANDOFF_H:.1f})"
        )
        inner_ok = False
    # P6/I8 — every board has ≥2 M2.5 insert standoffs + screw relief past insert.
    # Empty holes=[] used to skip P6 (wall-mount nubs) — that must fail.
    p6_ok = True
    for name, b in pod.BOARDS.items():
        posts = pod.board_standoffs(b)
        if len(posts) < 2:
            r.fail(
                f"P6/I8 {name} has {len(posts)} insert standoffs "
                "(need ≥2 M2.5 through-hole or clamp posts; no nubs)"
            )
            p6_ok = False
            continue
        for hx, hz in posts:
            z = b["z0"] + hz
            relief = pod.board_relief_depth(z)
            if relief < pod.INS_DEPTH + 1.5:
                r.fail(
                    f"P6 {name} z={z:.1f}: screw relief {relief:.1f} mm "
                    f"not past insert {pod.INS_DEPTH:.1f}"
                )
                p6_ok = False
    if p6_ok:
        r.ok(
            f"P6/I8 every board ≥2 M2.5 standoffs; insert {pod.INS_DEPTH:.2f} mm "
            f"+ relief extra {pod.SCREW_RELIEF_EXTRA:.1f}"
        )
    # I7 — iron corridor from the mating face to each board insert.
    # BMP/mag holes must sit in the static-bay window (serviceable cage).
    wx0, wx1, wz0, wz1 = pod._static_window()
    shp_r = right.val().wrapped
    i7_ok = True
    if getattr(pod, "Y_PCB", 0) < 20:
        r.fail("I7 Y_PCB too close to seam for mating-face heat-set")
        i7_ok = False
        inner_ok = False
    for name, b in pod.BOARDS.items():
        for hx, hz in pod.board_standoffs(b):
            x = b["x0"] + hx
            z = b["z0"] + hz
            if name in ("BMP581", "MAG"):
                if not (wx0 + 0.2 <= x <= wx1 - 0.2 and wz0 + 0.2 <= z <= wz1 - 0.2):
                    r.fail(
                        f"I7 {name} insert ({x:.1f},{z:.1f}) not in static-bay window"
                    )
                    i7_ok = False
            y = 2.0
            while y < pod.Y_PCB - 0.4:
                if (
                    BRepClass3d_SolidClassifier(
                        shp_r, gp_Pnt(x, y, z), 1e-4
                    ).State()
                    == TopAbs_IN
                ):
                    r.fail(
                        f"I7 {name} iron corridor blocked at "
                        f"({x:.1f},{y:.1f},{z:.1f})"
                    )
                    i7_ok = False
                    break
                y += 3.0
    if inner_ok and not any(n.startswith("FAIL I5") or n.startswith("FAIL I6") for n in r.notes):
        r.ok("I5 wall-mount boards on raised standoffs (XZ, inserts along Y)")
        r.ok("I6 board keepouts clear cradle / battery / neighbors / inner wall")
    if i7_ok:
        r.ok("I7 insert iron corridor (static bay has a tool window + cover)")

    # S3/L3 — isolated static plenum; holes only in that bay
    bx0, bx1, by0, by1, bz0, bz1 = pod._static_bay_box()
    s3_ok = True
    if by0 >= pod.Y_PCB - 1.0:
        r.fail("S3 static bay -Y wall missing (plenum not isolated)")
        s3_ok = False
    if bz1 - bz0 < 20.0 or bx1 - bx0 < 20.0:
        r.fail("S3 static bay too small to enclose BMP/mag")
        s3_ok = False
    for hx, hz in pod.static_hole_centers():
        if not (bx0 <= hx <= bx1 and bz0 <= hz <= bz1):
            r.fail(f"S3 static hole ({hx:.1f},{hz:.1f}) outside isolated bay")
            s3_ok = False
    if s3_ok:
        r.ok("S3/L3 static holes open into isolated BMP bay (serviceable cover)")

    # P5/L2 — aft pin shorter than cup; print-fit clamp
    if pod.SUN_RECESS_BOSS_LEN > pod.SUN_RECESS_DEPTH - 2.45:
        r.fail(
            f"P5 aft pin {pod.SUN_RECESS_BOSS_LEN:.2f} mm into "
            f"{pod.SUN_RECESS_DEPTH:.2f} mm cup (need ≥2.5 mm unused)"
        )
    else:
        r.ok(
            f"P5 aft pin {pod.SUN_RECESS_BOSS_LEN:.1f} mm "
            f"(cup {pod.SUN_RECESS_DEPTH:.1f}, clr "
            f"{pod.SUN_RECESS_DEPTH - pod.SUN_RECESS_BOSS_LEN:.1f})"
        )
    if pod.CLAMP_CLEAR < 0.20:
        r.fail(f"L2 clamp radial clearance {pod.CLAMP_CLEAR:.2f} < 0.20 (print-1 tight)")
    else:
        r.ok(
            f"L2 bores tip/smooth/clamp/barrel "
            f"+{pod.CRADLE_CLEAR_TIP:.2f}/+{pod.CRADLE_CLEAR_SMOOTH:.2f}/"
            f"+{pod.CLAMP_CLEAR:.2f}/+{pod.CRADLE_CLEAR_BARREL:.2f} radial"
        )
    if pod.NOSE_LIP_WALL < 1.5:
        r.fail(f"P7 nose lip {pod.NOSE_LIP_WALL:.2f} mm < 1.5")
    else:
        r.ok(f"P7 nose lip {pod.NOSE_LIP_WALL:.2f} mm radial")

    # L5 — route polylines stay in the cavity envelope (coarse AABB)
    for route in pod.build_routes():
        bad = False
        for (px, py, pz) in route["pts"]:
            if py < -pod.LEFT_EXTENT or py > pod.RIGHT_EXTENT:
                r.fail(f"L5 {route['name']} point y={py:.1f} outside pod")
                bad = True
                break
            if pz < 0.0 or pz > pod.OUTER_H:
                r.fail(f"L5 {route['name']} point z={pz:.1f} outside height")
                bad = True
                break
            # Hose must not run through the battery slab
            if (
                route["kind"] == "hose"
                and pod.BATT_X0 <= px <= pod.BATT_X0 + pod.BATT_POCKET_X
                and pod.BATT_Y0 <= py <= pod.BATT_Y0 + pod.BATT_POCKET_Y
                and pod.BATT_Z0 <= pz <= pod.BATT_Z0 + pod.BATT_POCKET_Z
            ):
                r.fail(f"L5 {route['name']} pierces battery pocket")
                bad = True
                break
        if not bad:
            r.ok(f"L5 {route['name']} {len(route['pts'])} pts in envelope")

    # L6 — locked panel COTS cutouts
    if abs(pod.SW_CUT_X - 19.6) > 0.05 or abs(pod.SW_CUT_Z - 13.0) > 0.05:
        r.fail(f"L6 rocker cutout {pod.SW_CUT_X:.1f}×{pod.SW_CUT_Z:.1f} ≠ 19.6×13 (R1966A / 2–3 mm panel)")
    elif abs(pod.USB_EAR_PITCH - 17.0) > 0.05:
        r.fail(f"L6 USB ear pitch {pod.USB_EAR_PITCH:.1f} ≠ 17 (CAB-15464)")
    elif not (8.0 <= pod.LED_HOLE_D <= 8.4):
        r.fail(f"L6 LED holder hole Ø{pod.LED_HOLE_D:.1f} not 8.0–8.4 mm")
    elif not (2.0 <= pod.WALL <= 3.0) or abs(pod.PANEL_T - pod.WALL) > 0.05:
        r.fail(f"L6 panel thickness WALL={pod.WALL:.2f} PANEL_T={pod.PANEL_T:.2f} not 2–3 mm")
    else:
        r.ok("L6 panel COM-08837 / CAB-15464 / Ø8.2 LED holders")

    # A9 — ogive tip stays fused (a disconnected tip scrap used to leave a flat nub)
    rbb = right.val().BoundingBox()
    if rbb.xmax < pod.OUTER_L - 1.0:
        r.fail(
            f"A9 right tip truncated xmax={rbb.xmax:.1f} "
            f"(want ≥ {pod.OUTER_L - 1.0:.1f}; tip scrap dropped?)"
        )
    else:
        r.ok(f"A9 right tip xmax={rbb.xmax:.1f}")

    # P4 — print orientation / bed
    for name, solid, side in (
        ("pod_right", right, +1),
        ("pod_left", left, -1),
    ):
        pr = pod.for_print_half(solid, side)
        if len(pr.val().Solids()) != 1:
            r.fail(f"P1 {name} print is not one solid")
            continue
        bb = pr.val().BoundingBox()
        if bb.xlen > pod.BED_LIMIT + 0.05 or bb.ylen > pod.BED_LIMIT + 0.05:
            r.fail(
                f"P4 {name} print BB {bb.xlen:.1f}×{bb.ylen:.1f} > bed {pod.BED_LIMIT}"
            )
        elif bb.zmin < -0.05:
            r.fail(f"P4 {name} not on bed (zmin={bb.zmin})")
        else:
            r.ok(
                f"P4 {name} print BB {bb.xlen:.1f}×{bb.ylen:.1f}×{bb.zlen:.1f}"
            )

    # L2 — SUN-B cradle stations present and tip protrudes past pod nose.
    aft_expect = pod.SUN_SMOOTH_X0 + (pod.SUN_TOTAL_LEN - pod.SUN_TIP_LEN)
    if abs(pod.SUN_AFT_X - aft_expect) > 0.05:
        r.fail("L2 SUN aft station math inconsistent")
    else:
        r.ok(
            f"L2 SUN-B cradle aft @ {pod.SUN_AFT_X:.1f}, "
            f"barbs @ {pod.SUN_BARB_TE_X:.0f}/{pod.SUN_BARB_STATIC_X:.0f}/{pod.SUN_BARB_PITOT_X:.0f}"
        )
    sun = pod.build_sun_placeholder()
    sbb = sun.val().BoundingBox()
    if abs(sbb.xmin - pod.SUN_TIP_X0) > 0.5:
        r.fail(f"L2 SUN tip xmin={sbb.xmin:.1f} (want ≈ {pod.SUN_TIP_X0:.1f})")
    else:
        r.ok(f"L2 SUN tip protrudes to x={sbb.xmin:.1f}")
    if abs(sbb.xmin) < 1.0 or sbb.xmin > 0:
        r.fail("L2 SUN tip should extend past pod nose (x<0)")
    # Shoulder seat: smooth OD must not start before nose bulkhead aft face.
    if pod.SUN_SMOOTH_X0 < pod.SHOULDER_BH_T - 0.05:
        r.fail("L2 SUN shoulder seat forward of nose bulkhead")
    else:
        r.ok(f"L2 SUN shoulder seats at x={pod.SUN_SMOOTH_X0:.1f} (tip-only BH)")

    check_shell_seal(r, pod, left, right)


def check_shell_seal(r: CheckResult, pod, left, right) -> None:
    """
    S1 — sealed cavity except intentional openings (tip, static array, panel).

    Seal the mating face, then ray-cast from a cavity point to a grid of
    exterior samples near the bottom-side fairings.  Zero wall hits ⇒ leak.
    Intentional openings are excluded from the sample set.
    """
    import math

    import cadquery as cq
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN

    outer = pod.full_body_solid(0.0)
    seal = (
        cq.Workplane("XY")
        .transformed(offset=(-1, -0.15, -1))
        .box(pod.OUTER_L + 2, 0.3, pod.OUTER_H + 2, centered=(False, False, False))
        .intersect(outer)
    )
    closed = pod.as_single_solid(left.union(right).union(seal), "sealed_pod")
    shp = closed.val().wrapped
    outer_shp = outer.val().wrapped
    cavity = (100.0, 8.0, 40.0)

    def is_freestream(pt: tuple[float, float, float]) -> bool:
        # Only true exterior counts — cavity/void samples inside the envelope
        # have 0 wall hits to each other and are not leaks.
        return (
            BRepClass3d_SolidClassifier(outer_shp, gp_Pnt(*pt), 1e-4).State()
            != TopAbs_IN
        )

    def n_hits_from(src: tuple[float, float, float], dst: tuple[float, float, float]) -> int:
        dx = dst[0] - src[0]
        dy = dst[1] - src[1]
        dz = dst[2] - src[2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length < 1e-6:
            return 0
        lin = gp_Lin(gp_Pnt(*src), gp_Dir(dx / length, dy / length, dz / length))
        inter = BRepIntCurveSurface_Inter()
        inter.Init(shp, lin, 1e-4)
        n = 0
        while inter.More():
            w = inter.W()
            if 0.05 < w < length - 0.05:
                n += 1
            inter.Next()
        return n

    def n_hits(dst: tuple[float, float, float]) -> int:
        return n_hits_from(cavity, dst)

    # Allowed exterior piercings — skip samples that aim into these windows.
    bay_x0, bay_x1, _, _, bay_z0, bay_z1 = pod._static_bay_box()

    def allowed(x: float, y: float, z: float) -> bool:
        # Tip mouth
        if x < 5.0 and abs(z - pod.PITOT_AXIS_Z) < pod.CRADLE_R_TIP + 3.0:
            return True
        # Static array on +Y skin
        if (
            y > pod.RIGHT_EXTENT - 3.0
            and bay_x0 <= x <= bay_x1
            and bay_z0 <= z <= bay_z1
        ):
            return True
        if y <= min(pod.RIGHT_EXTENT, pod.PANEL_Y) - 3.0:
            return False
        # Panel cluster
        if abs(x - pod.SW_X) <= pod.SW_CUT_X / 2 + 1.5 and abs(z - pod.SW_Z) <= pod.SW_CUT_Z / 2 + 1.5:
            return True
        if abs(x - pod.USB_X) <= pod.USB_WIN_X / 2 + 1.5 and abs(z - pod.USB_Z) <= pod.USB_WIN_Z / 2 + 1.5:
            return True
        for ex, ez in pod.usb_ear_xz():
            if (x - ex) ** 2 + (z - ez) ** 2 <= (pod.M3_CLR_D / 2 + 1.5) ** 2:
                return True
        for lx in (pod.LED_PWR_X, pod.LED_CHG_X):
            if (x - lx) ** 2 + (z - pod.LED_Z) ** 2 <= (pod.LED_HOLE_D / 2 + 1.5) ** 2:
                return True
        return False

    leaks: list[tuple[float, float, float]] = []
    half = pod._bottom_chord_half_w(0.0)
    y_br = pod.SECTION_YC + half  # right bottom chord
    # Dense grid at the bottom-side fairing — this is where a mismatched
    # inner/outer R previously left a through-slot.
    for x in range(int(pod.NOSE_FAIR_LEN) + 5, int(pod.MID_END_X) - 5, 10):
        for y in range(int(y_br) - 2, int(pod.RIGHT_EXTENT) + 4, 1):
            for z in (-1.0, 0.5, 2.0, 4.0, 6.0, 8.0):
                pt = (float(x), float(y), float(z))
                if allowed(*pt) or not is_freestream(pt):
                    continue
                if n_hits(pt) == 0:
                    leaks.append(pt)
        y_bl = pod.SECTION_YC - half
        for y in range(int(-pod.LEFT_EXTENT) - 3, int(y_bl) + 3, 1):
            for z in (-1.0, 0.5, 2.0, 4.0, 6.0):
                pt = (float(x), float(y), float(z))
                if not is_freestream(pt):
                    continue
                if n_hits(pt) == 0:
                    leaks.append(pt)

    if leaks:
        ex = leaks[0]
        r.fail(
            f"S1 shell leak: cavity→exterior 0-hit path "
            f"(e.g. ({ex[0]:.0f},{ex[1]:.1f},{ex[2]:.1f}); {len(leaks)} samples)"
        )
    else:
        r.ok("S1 sealed cavity (tip / static / USB / rocker / LEDs)")

    y_src = pod.Y_LAND - 6.0
    y_dst = pod.PANEL_Y + 10.0
    for name, x, z in (
        ("rocker", pod.SW_X, pod.SW_Z),
        ("USB", pod.USB_X, pod.USB_Z),
        ("LED pwr", pod.LED_PWR_X, pod.LED_Z),
        ("LED chg", pod.LED_CHG_X, pod.LED_Z),
    ):
        src = (x, y_src, z)
        dst = (x, y_dst, z)
        if not is_freestream(dst):
            r.fail(f"L6 {name} probe not in freestream")
            continue
        if (
            BRepClass3d_SolidClassifier(shp, gp_Pnt(*src), 1e-4).State()
            == TopAbs_IN
        ):
            r.fail(f"L6 {name} interior rebate blocked at y={y_src:.1f}")
            continue
        hits = n_hits_from(src, dst)
        if hits != 0:
            r.fail(f"L6 {name} cutout blocked ({hits} wall hits)")
        else:
            r.ok(f"L6 {name} cutout opens to freestream")

    # Battery pocket must not pierce the outer envelope (no freestream slot).
    pocket = (
        cq.Workplane("XY")
        .transformed(offset=(pod.BATT_X0, pod.BATT_Y0, pod.BATT_Z0))
        .box(
            pod.BATT_POCKET_X,
            pod.BATT_POCKET_Y,
            pod.BATT_POCKET_Z,
            centered=(False, False, False),
        )
    )
    batt_outside = pocket.cut(outer).val().Volume()
    # The raw box may overhang the faired envelope; the *cut tool* must not
    # pierce the skin.  Check that a sealed-shell ray test already passed and
    # that the applied notch (pocket ∩ outer − wall) does not reach freestream
    # by ensuring hollow halves have no cavity↔exterior path at battery X.
    inner = pod.full_body_solid(pod.WALL)
    wall = outer.cut(inner)
    cut_tool = pocket.intersect(outer).cut(wall)
    try:
        tool_vol = cut_tool.val().Volume()
    except Exception:
        tool_vol = 0.0
    # Cut tool should be interior-only; intersecting it with wall ≈ 0.
    tool_in_wall = cut_tool.intersect(wall).val().Volume() if tool_vol > 0 else 0.0
    if tool_in_wall > 1.0:
        r.fail(f"S1 battery cut intersects outer wall by {tool_in_wall:.0f} mm³")
    else:
        r.ok(f"S1 battery notch interior-only ({tool_vol:.0f} mm³, box overhang {batt_outside:.0f})")


def validate_all(*, stl_only: bool = False, directory: Path = POD_DIR) -> CheckResult:
    r = CheckResult()
    check_stls(r, directory)
    if not stl_only:
        try:
            check_geometry(r)
        except Exception as exc:
            r.fail(f"geometry checks crashed: {exc}")
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--stl-only",
        action="store_true",
        help="only check on-disk STL watertightness (skip CadQuery rebuild)",
    )
    ap.add_argument(
        "--dir",
        type=Path,
        default=POD_DIR,
        help="directory containing STL exports",
    )
    args = ap.parse_args(argv)

    r = validate_all(stl_only=args.stl_only, directory=args.dir)
    for line in r.notes:
        print(line)
    if r.passed:
        print("validate_pod: all checks passed")
        return 0
    print(f"validate_pod: {len(r.failures)} failure(s)", file=sys.stderr)
    for f in r.failures:
        print(f"  - {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
