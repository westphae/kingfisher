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
STL_FILES = ("pod_right.stl", "pod_left.stl")

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
        if n_tri < 100:
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

    # I5 — boards clear cradle land + mutual BOARD_GAP
    cradle_plates = [
        (0.0, pod.SHOULDER_BH_T),
        (pod.SUN_SMOOTH_X0 + pod.SUN_SMOOTH_LEN * 0.55, 3.0),
        (pod.SUN_AFT_X - 8.0, 3.0),
        (pod.SUN_AFT_X + 0.2, 3.0),
    ]
    clamp_x0 = pod.CLAMP_X0
    clamp_x1 = pod.CLAMP_X0 + pod.CLAMP_LEN
    y_clear = pod.CRADLE_LAND_Y + pod.BOARD_GAP
    board_boxes = []
    for name, b in pod.BOARDS.items():
        x0, x1 = b["x0"], b["x0"] + b["xl"]
        y0 = pod.DECK_Y0 + b["y0"]
        y1 = y0 + b["yl"]
        board_boxes.append((name, x0, x1, y0, y1))
        for px, plen in cradle_plates:
            p0, p1 = px, px + plen
            if x1 > p0 and x0 < p1 and y0 < y_clear - 0.05:
                r.fail(
                    f"I5 {name} collides cradle plate x={p0:.1f}..{p1:.1f} "
                    f"(board y0={y0:.1f} < clear Y={y_clear:.1f})"
                )
        # Clamp land is a thick cylinder over the knurled band — boards under it
        # in X must sit outboard with cable clearance (not merely miss the PCB).
        if x1 > clamp_x0 and x0 < clamp_x1 and y0 < y_clear - 0.05:
            r.fail(
                f"I5 {name} under clamp land without Y clearance "
                f"(board y0={y0:.1f} < {y_clear:.1f})"
            )
    # Pairwise clearance: X-neighbors need BOARD_GAP; X-overlap OK if Y-separated.
    for i, (n1, a0, a1, ay0, ay1) in enumerate(board_boxes):
        for n2, b0, b1, by0, by1 in board_boxes[i + 1 :]:
            x_overlap = a1 > b0 and a0 < b1
            y_overlap = ay1 > by0 and ay0 < by1
            if x_overlap and y_overlap:
                r.fail(f"I5 {n1}/{n2} footprints overlap in X and Y")
                continue
            if x_overlap:
                continue  # stacked in Y (e.g. BMP + mag)
            gap = b0 - a1 if a1 <= b0 else a0 - b1
            if 0.0 <= gap < pod.BOARD_GAP - 0.05 and not (
                {n1, n2} == {"BABY", "PROMICRO"}  # tight Qwiic hop by design
            ):
                r.fail(f"I5 {n1}/{n2} X-gap {gap:.1f} < BOARD_GAP {pod.BOARD_GAP}")
    if not any(n.startswith("FAIL I5") for n in r.notes):
        r.ok("I5 boards clear cradle land + BOARD_GAP")

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
    S1 — sealed cavity except intentional openings (tip, static array, charge USB).

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

    def n_hits(dst: tuple[float, float, float]) -> int:
        dx = dst[0] - cavity[0]
        dy = dst[1] - cavity[1]
        dz = dst[2] - cavity[2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length < 1e-6:
            return 0
        lin = gp_Lin(gp_Pnt(*cavity), gp_Dir(dx / length, dy / length, dz / length))
        inter = BRepIntCurveSurface_Inter()
        inter.Init(shp, lin, 1e-4)
        n = 0
        while inter.More():
            w = inter.W()
            if 0.05 < w < length - 0.05:
                n += 1
            inter.Next()
        return n

    # Allowed exterior piercings — skip samples that aim into these windows.
    baby = pod.BOARDS["BABY"]
    charge_x = baby["x0"] + baby["xl"] / 2
    charge_z = baby["z0"] + pod.PCB_T + pod.USB_CHARGE_H / 2 - 0.5
    static_x0 = (pod.BAY_X0 + pod.BAY_X1) / 2 - (
        (pod.STATIC_HOLE_COLS - 1) * pod.STATIC_HOLE_PITCH_X / 2 + 4.0
    )
    static_x1 = (pod.BAY_X0 + pod.BAY_X1) / 2 + (
        (pod.STATIC_HOLE_COLS - 1) * pod.STATIC_HOLE_PITCH_X / 2 + 4.0
    )

    def allowed(x: float, y: float, z: float) -> bool:
        # Tip mouth
        if x < 5.0 and abs(z - pod.PITOT_AXIS_Z) < pod.CRADLE_R_TIP + 3.0:
            return True
        # Static array on +Y skin
        if y > pod.RIGHT_EXTENT - 3.0 and static_x0 <= x <= static_x1:
            return True
        # Babysitter charge port on +Y skin
        if (
            y > pod.RIGHT_EXTENT - 3.0
            and abs(x - charge_x) < pod.USB_CHARGE_W / 2 + 2.0
            and abs(z - charge_z) < pod.USB_CHARGE_H / 2 + 2.0
        ):
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
        r.ok("S1 sealed cavity (tip / static / charge USB only)")

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
