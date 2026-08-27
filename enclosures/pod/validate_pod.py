#!/usr/bin/env python3
"""
Validate wing pod v3 geometry + exported STLs against REQUIREMENTS.md.

  cd enclosures/pod && uv run --project .. python validate_pod.py
  cd enclosures/pod && uv run --project .. python validate_pod.py --stl-only

Exit 0 on success, 1 on any failure.  Called by wing_pod_v3.py after export.

Why this file grew teeth
------------------------
v2 shipped a panel pad that was an ~80 x 3.5 mm open slot into the electronics
bay, and every check here passed.  The mesh was watertight, the halves were
single solids, and the seal test only sampled six z levels near the bottom
fairing (z in {-1, 0.5, 2, 4, 6, 8}) so it never looked at z~70 where the hole
was.  W1 (minimum wall) and S1' (full 3-D leak) exist so that class of defect
cannot pass again, and A10-A12 turn "it looks aerodynamic" into numbers.
"""
from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter
from pathlib import Path

POD_DIR = Path(__file__).resolve().parent
STL_FILES = ("pod_right.stl", "pod_left.stl", "tail_panel.stl",
             "static_bay.stl", "pm_cover.stl")
SMALL_PARTS = ("tail_panel.stl", "static_bay.stl", "pm_cover.stl")


class CheckResult:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def ok(self, msg: str) -> None:
        self.notes.append(f"OK   {msg}")

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
    if len(data) != 84 + n * 50:
        return n, -2
    edges: Counter = Counter()
    for i in range(n):
        off = 84 + i * 50
        vals = struct.unpack_from("<12f", data, off)
        verts = [tuple(round(vals[3 + 3 * k + j], ndigits) for j in range(3))
                 for k in range(3)]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edges[tuple(sorted((verts[a], verts[b])))] += 1
    return n, sum(1 for c in edges.values() if c == 1)


def check_fresh(r: CheckResult, directory: Path = POD_DIR) -> None:
    """P11: the exported artifacts must not predate the model that made them.

    Printed artifacts are committed only at a working version, so between those
    commits the tracked .stl/.step legitimately lag the script — and nothing
    otherwise tells you.  Printing a stale STL is the expensive mistake this
    catches: the parts on the bed silently miss every fix made since.
    """
    import hashlib
    model = directory / "wing_pod_v3.py"
    if not model.is_file():
        return
    want = f"kingfisher wing_pod_v3 {hashlib.sha256(model.read_bytes()).hexdigest()[:16]}"
    stale = []
    for name in STL_FILES:
        f = directory / name
        if not f.is_file():
            continue
        got = f.read_bytes()[:80].rstrip(b"\0").decode("ascii", "replace")
        if got != want:
            stale.append(name)
    if stale:
        r.fail(f"P11 {len(stale)} STL(s) were not built from the current "
               f"wing_pod_v3.py — rerun before printing: {', '.join(sorted(stale))}")
    else:
        r.ok(f"P11 all {len(STL_FILES)} STLs stamped with the current model hash")


def check_stls(r: CheckResult, directory: Path = POD_DIR) -> None:
    """P1-P3: present, watertight, tessellated tightly enough."""
    for name in STL_FILES:
        path = directory / name
        if not path.is_file():
            r.fail(f"P1/P2 missing {name}")
            continue
        n_tri, n_bound = stl_boundary_edges(path)
        if n_bound == -1:
            r.fail(f"P2 {name}: too short to be a binary STL")
        elif n_bound == -2:
            r.fail(f"P2 {name}: binary size mismatch vs triangle count")
        elif n_tri < (20 if name in SMALL_PARTS else 100):
            r.fail(f"P2 {name}: only {n_tri} triangles")
        elif n_bound != 0:
            r.fail(f"P2 {name}: not watertight ({n_bound} boundary edges) — "
                   "AnkerMake will reject it; tighten tessellation")
        else:
            r.ok(f"P2 {name}: {n_tri} tris, watertight")


def check_print_volume(r: CheckResult, pod) -> None:
    """P0 — the constraint that decided every length in this design."""
    import math
    limit = pod.BED_LIMIT * math.sqrt(2.0)
    if pod.OUTER_L + pod.OUTER_H > limit:
        r.fail(f"P0 OUTER_L+OUTER_H = {pod.OUTER_L + pod.OUTER_H:.1f} > {limit:.1f} "
               "(45 deg flange-down AABB will not fit the M5C bed)")
    else:
        r.ok(f"P0 L+H = {pod.OUTER_L + pod.OUTER_H:.1f} <= {limit:.1f}; "
             f"45 deg AABB {(pod.OUTER_L + pod.OUTER_H) / math.sqrt(2.0):.1f} "
             f"<= {pod.BED_LIMIT:.0f} mm")


def check_interior(r: CheckResult, pod) -> None:
    """I1/I5/I6/I8 + L1/L2/L6 — layout rules that are cheap to state as numbers."""
    # I8 is a RESTRAINT rule, not a hole count.  The old test was
    # len(holes) < 2, which existed to catch a board whose fasteners had been
    # forgotten.  With the holes now measured that test is simply wrong: the
    # MMC5983 has one hole, the BMP581 two on a single edge, and the Pro Micro
    # none at all, and no amount of asserting will give them more.  What has
    # to hold is that nothing can rotate or rock.
    for name, b in pod.BOARDS.items():
        screws = pod.board_standoffs(b)
        nubs = pod.board_nubs(b)
        tray = pod.board_tray_screws(b)
        keepers = 2 if b.get("keepers") else 0
        if b.get("cover"):
            pads, cs = len(pod.pm_support_pads()), len(pod.pm_cover_screws())
            if pads >= 3 and cs >= 3:
                r.ok(f"I8 {name} restrained by {pads} pads + a {cs}-screw cover")
            else:
                r.fail(f"I8 {name} declares a cover but has {pads} pads / "
                       f"{cs} cover screws")
        elif len(tray) >= 3:
            r.ok(f"I8 {name} restrained by a {len(tray)}-screw tray")
        elif len(screws) >= 2:
            r.ok(f"I8 {name} restrained by {len(screws)} screws"
                 + (f" + {len(nubs)} nubs" if nubs else ""))
        elif len(screws) == 1 and (len(nubs) + keepers) >= 2:
            r.ok(f"I8 {name} restrained by 1 screw + {len(nubs)} nub(s) "
                 f"+ {keepers} keeper(s)")
        else:
            r.fail(f"I8 {name} is not restrained: {len(screws)} screws, "
                   f"{len(nubs)} nubs, {keepers} keepers, {len(tray)} tray screws "
                   "— it can rotate or rock")
    if pod.Y_PCB <= pod.Y_LAND - pod.STANDOFF_H - 0.01:
        r.fail("I5 boards are flush on the land, not on raised standoffs")
    else:
        r.ok(f"I5 boards on {pod.STANDOFF_H:.1f} mm standoffs, inserts along Y")
    # I7 proper is check_insert_access(), which sweeps a real corridor.  Here we
    # only gate the band the flange rails leave, so a board dragged down into a
    # rail is caught at import rather than after a 10 minute solid build.
    lo = [i for i in pod.insert_inventory()
          if i["access"] == "seam" and i["y"] > 0.6
          and not (pod.INSERT_Z_MIN - 0.05 <= i["z"] <= pod.INSERT_Z_MAX + 0.05)]
    if lo:
        r.fail(f"I7 {len(lo)} pilot(s) outside the z band "
               f"{pod.INSERT_Z_MIN:.1f}..{pod.INSERT_Z_MAX:.1f} the flange rails leave")
    else:
        r.ok(f"I7 all seam pilots inside the z band {pod.INSERT_Z_MIN:.1f}.."
             f"{pod.INSERT_Z_MAX:.1f} left by the flange rails")

    if not (0.20 <= pod.CLAMP_CLEAR):
        r.fail(f"L2 clamp clearance {pod.CLAMP_CLEAR:.2f} below the print-1 slip fit")
    if pod.SUN_RECESS_DEPTH - pod.SUN_RECESS_BOSS_LEN < 2.5:
        r.fail("P5 locating boss leaves less than 2.5 mm of the cup unused")
    else:
        r.ok(f"L2/P5 SUN protrudes {pod.SUN_PROTRUDE:.1f} mm, stop at x="
             f"{pod.NOSE_BH_X1:.2f}, aft face x={pod.SUN_AFT_X:.2f}, "
             f"boss {pod.SUN_RECESS_BOSS_LEN:.2f} in a {pod.SUN_RECESS_DEPTH:.2f} cup")
    if pod.NOSE_LIP_WALL < 1.5:
        r.fail(f"P7 nose lip {pod.NOSE_LIP_WALL:.2f} mm < 1.5")
    else:
        r.ok(f"P7 nose lip {pod.NOSE_LIP_WALL:.2f} mm radial")

    if abs(pod.SW_CUT_Y - 19.6) > 0.05 or abs(pod.SW_CUT_Z - 13.0) > 0.05:
        r.fail("L6 rocker cutout is not 19.6 x 13.0 (COM-08837 / R1966A)")
    elif abs(pod.USB_EAR_PITCH - 17.0) > 0.05:
        r.fail("L6 USB ear pitch is not 17 mm (CAB-15464)")
    elif not (8.0 <= pod.LED_HOLE_D <= 8.4):
        r.fail(f"L6 LED holder hole {pod.LED_HOLE_D:.1f} outside 8.0-8.4")
    elif not (2.0 <= pod.PANEL_T <= 3.0):
        r.fail(f"L6 panel thickness {pod.PANEL_T:.1f} outside the R1966A 2-3 mm band")
    else:
        r.ok("L6 aft panel: COM-08837 / CAB-15464 / two Ø8.2 LED holders")

    clear = pod.OUTER_H - 2 * (pod.WALL + pod.BATT_SEAL_KEEP)
    if pod.BATT_POCKET_Z > clear:
        r.fail(f"L1 battery pocket {pod.BATT_POCKET_Z:.1f} > {clear:.1f} mm clear "
               "between the flange rails")
    else:
        r.ok(f"L1 battery {pod.BATT_X:.0f}x{pod.BATT_Y:.0f}x{pod.BATT_Z:.0f} laid down; "
             f"pocket {pod.BATT_POCKET_Z:.1f} in {clear:.1f} mm between rails")

    frac = pod.STATIC_PORT_X / pod.OUTER_L
    if not (0.40 <= frac <= 0.60):
        r.fail(f"L3 static ports at {frac:.0%} of length — outside the 40-60% band "
               "where a side port reads closest to freestream")
    else:
        r.ok(f"L3 static ports at {frac:.0%} of body length")
    for hx, hz in pod.static_hole_centers():
        if not (pod.BAY_X0 < hx < pod.BAY_X1 and pod.BAY_Z0 < hz < pod.BAY_Z1):
            r.fail(f"S3 static hole ({hx:.1f},{hz:.1f}) is outside the isolated bay")
            break
    else:
        r.ok("S3 static holes open only into the isolated BMP plenum")


def _pod_module():
    """Reuse the already-loaded model when wing_pod_v3.py is the running
    script.  A plain `import wing_pod_v3` there creates a SECOND module object
    and rebuilds both halves from scratch, doubling a ~4 minute run."""
    main_mod = sys.modules.get("__main__")
    if getattr(main_mod, "__file__", "").endswith("wing_pod_v3.py"):
        return main_mod
    import wing_pod_v3 as pod
    return pod


def check_geometry(r: CheckResult) -> None:
    pod = _pod_module()
    import validate_pod_v3_checks as g

    check_print_volume(r, pod)
    check_interior(r, pod)
    g.check_no_invented_holes(r, pod)
    g.check_board_envelopes(r, pod)
    g.check_parts_against_each_other(r, pod)
    g.check_print_pose(r, pod)
    g.check_holddown_footprints(r, pod)
    g.check_aero(r, pod)
    g.check_flats(r, pod)

    right = pod.as_single_solid(pod.build_right(), "pod_right")
    left = pod.as_single_solid(pod.build_left(), "pod_left")
    g.check_tips(r, pod, left, right)
    g.check_open_bay(r, pod, left, right)
    g.check_insert_access(r, pod, right)
    g.check_part_interference(r, pod, left, right)
    g.check_fastener_holes(r, pod, left, right)

    # I2 — nothing may poke outside the envelope.
    outer = pod.full_body_solid(0.0)
    for name, half in (("pod_right", right), ("pod_left", left)):
        extra = half.val().cut(outer.val()).Volume()
        if extra > 25.0:
            r.fail(f"I2 {name} protrudes {extra:.1f} mm^3 outside the envelope")
        else:
            r.ok(f"I2 {name} outside envelope {extra:.2f} mm^3")

    # Assemble the real sealed pod: both halves, the seam, and the aft plate.
    import cadquery as cq
    seam = (
        cq.Workplane("XY")
        .transformed(offset=(-1.0, -0.15, -1.0))
        .box(pod.OUTER_L + 2, 0.3, pod.OUTER_H + 2, centered=(False, False, False))
        .intersect(outer)
    )
    closed = pod.as_single_solid(
        left.union(right).union(seam).union(pod.build_tail_panel()), "sealed_pod"
    )
    g.check_min_wall(r, pod, closed)
    g.check_leak(r, pod, closed)
    g.check_drain(r, pod, closed)


def validate_all(*, stl_only: bool = False, directory: Path = POD_DIR) -> CheckResult:
    r = CheckResult()
    check_fresh(r, directory)
    check_stls(r, directory)
    if not stl_only:
        try:
            check_geometry(r)
        except Exception as exc:  # noqa: BLE001
            r.fail(f"geometry checks crashed: {type(exc).__name__}: {exc}")
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stl-only", action="store_true")
    ap.add_argument("--dir", default=str(POD_DIR))
    args = ap.parse_args(argv)
    r = validate_all(stl_only=args.stl_only, directory=Path(args.dir))
    for note in r.notes:
        print(note)
    if r.passed:
        print("validate_pod: all checks passed")
        return 0
    print(f"validate_pod: {len(r.failures)} failure(s)", file=sys.stderr)
    for f in r.failures:
        print(f"  - {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
