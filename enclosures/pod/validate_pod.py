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
STL_FILES = ("pod_right.stl", "pod_left.stl", "pitot_plug.stl")

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


def _planar_butt_area(solid, x_lo: float, x_hi: float, want_fwd: bool) -> float:
    """Area of nearly planar faces with |nx|~1 and thin X extent in [x_lo, x_hi]."""
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
            n = f.normalAt(f.Center())
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

    nose_butt = _planar_butt_area(
        outer, pod.NOSE_FAIR_LEN - 3.0, pod.NOSE_FAIR_LEN + 4.0, want_fwd=True
    )
    tail_butt = _planar_butt_area(
        outer, pod.MID_END_X - 4.0, pod.MID_END_X + 3.0, want_fwd=False
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

    plug = pod.as_single_solid(pod.build_plug(), "pitot_plug")
    pp = pod.for_print_plug(plug)
    zlen = pp.val().BoundingBox().zlen
    expect = pod.PLUG_FLANGE_T + pod.PLUG_LEN
    if abs(zlen - expect) > 0.2:
        r.fail(f"P5 plug print height {zlen:.1f} ≠ FLANGE_T+PLUG_LEN {expect:.1f}")
    else:
        r.ok(f"P5 plug print height {zlen:.1f}")


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
