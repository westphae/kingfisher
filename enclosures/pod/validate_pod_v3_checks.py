"""Geometry checks for wing pod v3 — imported by validate_pod.py.

Split out so the STL-only path stays importable without CadQuery.
"""
from __future__ import annotations

import math


def _tri_samples(shape, tol: float = 0.4, max_samples: int = 0):
    """Triangle centroids + outward normals of a tessellated solid.

    `max_samples` strides the triangle list so these checks stay minutes, not
    hours: each sample costs an OCCT solid classification or ray cast against a
    ~2000-face solid.  Coverage still matters more than density — the v2 panel
    hole was ~280 mm^2 of a ~57000 mm^2 skin, so even 4000 samples put ~20
    points inside it.
    """
    verts, tris = shape.tessellate(tol)
    if max_samples and len(tris) > max_samples:
        step = len(tris) // max_samples + 1
        tris = tris[::step]
    for a, b, c in tris:
        pa, pb, pc = verts[a], verts[b], verts[c]
        cx = (pa.x + pb.x + pc.x) / 3.0
        cy = (pa.y + pb.y + pc.y) / 3.0
        cz = (pa.z + pb.z + pc.z) / 3.0
        ux, uy, uz = pb.x - pa.x, pb.y - pa.y, pb.z - pa.z
        vx, vy, vz = pc.x - pa.x, pc.y - pa.y, pc.z - pa.z
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        n = math.sqrt(nx * nx + ny * ny + nz * nz)
        if n < 1e-9:
            continue
        area = 0.5 * n
        yield (cx, cy, cz), (nx / n, ny / n, nz / n), area


def _openings(pod):
    """Every place the outer skin is ALLOWED to be open (S1).

    Anything not listed here that fails the wall test is a defect, which is the
    whole point: v2's panel pad was an undeclared 80 x 3.5 mm slot and the old
    six-z-level leak grid never sampled it.

    "Recessed" matters as much as "open": a counterbore is a deliberate dish in
    the aero skin, so W1 measuring inward from the IDEAL envelope will always
    land inside it.  Declaring it is not weakening the check — the material
    that must remain BEHIND the counterbore is gated analytically in
    wing_pod_v3 (LEFT_EXTENT - LID_CB_DEPTH >= 1.5), which is the property that
    actually matters and which W1 cannot see.
    """
    holes = pod.static_hole_centers()
    drain_cy = 0.5 * (pod.DRAIN_Y0 + pod.DRAIN_Y1)

    def inside(pt):
        x, y, z = pt
        # 1. Prandtl / SUN tip mouth
        if x < pod.NOSE_BH_X1 + 1.0:
            # Measured from the PITOT AXIS, not from y=0.  Same stale
            # centreline the SUN insertion probe had: the mouth moved to
            # y=-15 with the clamshell inversion, so a sample 5.4 mm from the
            # real axis read as 19.9 mm from the assumed one and the mouth
            # stopped counting as a declared opening.
            if math.hypot(y - pod.PITOT_AXIS_Y,
                          z - pod.PITOT_AXIS_Z) < pod.CRADLE_R_CLAMP + 2.5:
                return True
        # 2. multi-hole static array into the isolated BMP bay
        if y > pod.RIGHT_EXTENT - 4.0:
            for hx, hz in holes:
                if math.hypot(x - hx, z - hz) < pod.STATIC_HOLE_D / 2 + 1.5:
                    return True
        # 3. the whole aft face is the service-panel joint, not skin: the plate
        #    seals it from OUTSIDE the envelope, so "material behind this face"
        #    is the wrong question there.  Its own cluster cuts are covered by
        #    the same clause.
        if x > pod.OUTER_L - 1.0:
            return True
        if x > pod.OUTER_L - 3.0:
            cy = pod.PANEL_CY
            if (abs(y - cy) <= pod.SW_CUT_Y / 2 + 1.5
                    and abs(z - pod.SW_Z) <= pod.SW_CUT_Z / 2 + 1.5):
                return True
            if (abs(y - cy) <= pod.USB_WIN_Y / 2 + 1.5
                    and abs(z - pod.USB_Z) <= pod.USB_WIN_Z / 2 + 1.5):
                return True
            for dy in (-pod.USB_EAR_PITCH / 2, pod.USB_EAR_PITCH / 2):
                if math.hypot(y - (cy + dy), z - pod.USB_Z) <= pod.M3_CLR_D / 2 + 1.5:
                    return True
            for dy in (-pod.LED_DY, pod.LED_DY):
                if math.hypot(y - (cy + dy), z - pod.LED_Z) <= pod.LED_HOLE_D / 2 + 1.5:
                    return True
        # 4. flange screw counterbores: a designed recess in the left cover so
        #    the M2.5 head sits flush in the aero skin.  Left half only.
        if y < 0.0:
            for sx, sz in pod.FLANGE_SCREWS:
                if math.hypot(x - sx, z - sz) <= pod.LID_CB_D / 2 + 1.5:
                    return True
        # 5. labyrinth drain
        if (z < pod.INNER_Z0 + 1.0
                and pod.DRAIN_X0 <= x <= pod.DRAIN_X1
                and abs(y - drain_cy) < pod.DRAIN_D / 2 + 2.0):
            return True
        return False

    return inside


def check_min_wall(r, pod, closed) -> None:
    """W1 — the direct gate on the v2 failure.

    Step inward from the outer skin by a fraction of WALL along the surface
    normal.  That point must land in material.  Where v2's panel pad stood
    proud of the ellipse the shell had ZERO wall at the pad perimeter, so this
    test fails there immediately; the old checks all passed because the flap
    was still a valid, watertight, single solid.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON

    outer = pod.full_body_solid(0.0).val()
    cls = BRepClass3d_SolidClassifier(closed.val().wrapped)
    ocls = BRepClass3d_SolidClassifier(outer.wrapped)
    allowed = _openings(pod)
    probe = pod.WALL * 0.55
    bad = []
    bad_area = 0.0
    total = 0
    for pt, n, area in _tri_samples(outer, 0.5, max_samples=5000):
        total += 1
        if allowed(pt):
            continue
        # A tessellated triangle's winding follows its FACE orientation, and a
        # face can sit REVERSED in the solid, so the raw cross-product normal
        # points inward on some faces.  Orient it against the envelope itself
        # instead of trusting the sign: stepping the wrong way lands in open
        # air and reports a hole that is not there (125 false positives here).
        eps = 0.05
        ocls.Perform(gp_Pnt(pt[0] + n[0] * eps, pt[1] + n[1] * eps,
                            pt[2] + n[2] * eps), 1e-7)
        if ocls.State() == TopAbs_IN:
            n = (-n[0], -n[1], -n[2])
        q = (pt[0] - n[0] * probe, pt[1] - n[1] * probe, pt[2] - n[2] * probe)
        # the seam plane is a mating face, not skin; skip a thin band there
        if abs(q[1]) < 0.6:
            continue
        cls.Perform(gp_Pnt(*q), 1e-7)
        if cls.State() in (TopAbs_IN, TopAbs_ON):
            continue
        # Confirm with an independent inward direction before calling it a
        # defect.  A triangle normal is a heuristic and goes wrong on slivers
        # and at high curvature; on the smooth loft that produced 2 false
        # positives out of 4774 at a spot where the wall measures exactly
        # 2.50 mm.  The vector toward the section centre is reliable for this
        # convex-ish family, and a REAL hole fails both tests — stepping
        # toward the centre from a hole still lands in the cavity.
        sy0, sy1, sz0, sz1 = pod.section_params(pt[0])[:4]
        cy, cz = 0.5 * (sy0 + sy1), 0.5 * (sz0 + sz1)
        dy, dz = cy - pt[1], cz - pt[2]
        mag = math.hypot(dy, dz)
        if mag > 1e-6:
            q2 = (pt[0], pt[1] + dy / mag * probe, pt[2] + dz / mag * probe)
            cls.Perform(gp_Pnt(*q2), 1e-7)
            if cls.State() in (TopAbs_IN, TopAbs_ON):
                continue
        bad.append(pt)
        bad_area += area
    if bad:
        x, y, z = bad[0]
        r.fail(
            f"W1 wall thinner than {probe:.2f} mm at {len(bad)}/{total} skin samples "
            f"({bad_area:.0f} mm^2), e.g. ({x:.1f},{y:.1f},{z:.1f}) — "
            "undeclared opening or a feature standing proud of the skin"
        )
    else:
        r.ok(f"W1 wall >= {probe:.2f} mm over {total} skin samples "
             f"(openings: tip mouth, static array, aft panel, drain)")


def check_leak(r, pod, closed) -> None:
    """S1' — full 3-D leak test.

    v2 sampled six z levels near the bottom fairing only.  This walks the whole
    outer skin and casts a ray inward; a sample with no wall crossing before the
    cavity is a hole.
    """
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN

    outer = pod.full_body_solid(0.0).val()
    shp = closed.val().wrapped
    ocls = BRepClass3d_SolidClassifier(outer.wrapped)
    allowed = _openings(pod)
    leaks = []
    total = 0
    for pt, n, _area in _tri_samples(outer, 0.9, max_samples=2500):
        if allowed(pt):
            continue
        # Same reversed-face hazard as W1 — orient outward against the envelope.
        ocls.Perform(gp_Pnt(pt[0] + n[0] * 0.05, pt[1] + n[1] * 0.05,
                            pt[2] + n[2] * 0.05), 1e-7)
        if ocls.State() == TopAbs_IN:
            n = (-n[0], -n[1], -n[2])
        src = (pt[0] + n[0] * 2.0, pt[1] + n[1] * 2.0, pt[2] + n[2] * 2.0)
        if abs(src[1]) < 1.0:
            continue
        total += 1
        lin = gp_Lin(gp_Pnt(*src), gp_Dir(-n[0], -n[1], -n[2]))
        inter = BRepIntCurveSurface_Inter()
        inter.Init(shp, lin, 1e-4)
        hits = 0
        while inter.More():
            if 0.05 < inter.W() < 12.0:
                hits += 1
            inter.Next()
        if hits == 0:
            leaks.append(pt)
    if leaks:
        x, y, z = leaks[0]
        r.fail(f"S1 leak: {len(leaks)}/{total} skin samples have no wall behind them, "
               f"e.g. ({x:.1f},{y:.1f},{z:.1f})")
    else:
        r.ok(f"S1 sealed cavity over {total} skin samples")


def check_aero(r, pod) -> None:
    """A10/A11/A12 — the OML is smooth and the boattail keeps flow attached."""
    # A10: C1 continuity of the section laws.  v2 could not state this because
    # nose/mid/tail were separate solids; here it is a property of one family.
    n = 2000
    worst_d2 = 0.0
    worst_x = 0.0
    prev = None
    prev_slope = None
    for i in range(n + 1):
        x = pod.OUTER_L * i / n
        cur = pod.section_params(x)
        if prev is not None:
            h = pod.OUTER_L / n
            slope = [(cur[k] - prev[k]) / h for k in range(4)]
            if prev_slope is not None:
                jump = max(abs(slope[k] - prev_slope[k]) for k in range(4))
                if jump > worst_d2:
                    worst_d2, worst_x = jump, x
            prev_slope = slope
        prev = cur
    if worst_d2 > 0.06:
        r.fail(f"A10 slope discontinuity {worst_d2:.3f} at x={worst_x:.1f} "
               "(step or kink in the outer mold line)")
    else:
        r.ok(f"A10 OML C1-continuous (max slope jump {worst_d2:.4f})")

    if pod.NOSE_ANGLE > 45.0:
        r.fail(f"A13 nose surface angle {pod.NOSE_ANGLE:.1f} deg > 45")
    else:
        r.ok(f"A13 nose max angle {pod.NOSE_ANGLE:.1f} deg (favourable gradient; "
             "gated loosely on purpose, unlike the boattail)")

    ang = pod.max_boattail_angle()
    if ang > 12.0:
        r.fail(f"A11 boattail surface angle {ang:.1f} deg > 12 (separation risk)")
    else:
        r.ok(f"A11 boattail max angle {ang:.1f} deg")

    ratio = pod.BASE_A / pod.FRONTAL_A
    if ratio > 0.70:
        r.fail(f"A12 base/frontal {ratio:.2f} > 0.70")
    else:
        r.ok(f"A12 fineness {pod.FINENESS:.2f}, base/frontal {ratio:.2f}, "
             f"nose {pod.NOSE_LEN / pod.D_EQ:.2f}D, boattail {pod.TAIL_LEN / pod.D_EQ:.2f}D")


def check_flats(r, pod) -> None:
    """A6/A7 — the wing-mate face and the standing face are really planar."""
    outer = pod.full_body_solid(0.0).val()
    for z, name, tag in ((pod.OUTER_H, "flat top", "A6"), (0.0, "flat bottom", "A7")):
        faces = [f for f in outer.Faces()
                 if f.geomType() == "PLANE" and abs(f.Center().z - z) < 1e-6]
        area = sum(f.Area() for f in faces)
        if area < 1500.0:
            r.fail(f"{tag} {name} area {area:.0f} mm^2 too small ({len(faces)} faces)")
        else:
            r.ok(f"{tag} {name} planar, {area:.0f} mm^2 over {len(faces)} faces")


def check_tips(r, pod, left, right) -> None:
    """A8/A9 — the OML reaches both tips; no dropped tail scrap.

    Both halves used to have to reach the nose, because the seam ran down the
    middle and the nose mouth was split between them.  Inverting the clamshell
    ended that: the SUN, its cradle and the whole nose now live in the bowl,
    and the plate is a flat lid over the board bay that legitimately starts
    aft of the nose cone.  Requiring the plate to reach x=0 would demand
    material where the design deliberately has none.  The tail is different --
    both halves still run out to the tail rim, so A9 stays on both.
    """
    for name, half in (("pod_left", left), ("pod_right", right)):
        bb = half.val().BoundingBox()
        if bb.xmin > 1.0 and name == "pod_left":
            r.fail(f"A8 {name} does not reach the nose (xmin={bb.xmin:.1f})")
        elif bb.xmax < pod.OUTER_L - 0.5:
            r.fail(f"A9 {name} tail tip not fused (xmax={bb.xmax:.1f})")
        else:
            r.ok(f"A8/A9 {name} spans x {bb.xmin:.2f}..{bb.xmax:.2f}")


def check_drain(r, pod, closed) -> None:
    """L8 — the drain must be a labyrinth, not a hole.

    The invariant is that there is no STRAIGHT path from freestream to the
    cavity: fire a ray up through the outer drain hole and it must run into
    the channel roof.

    This used to count ray/surface crossings and demand exactly two, on the
    reasoning that the ray sees the roof's under- and upper- surfaces.  That
    stopped being true once the board land grew over the drain: the roof is now
    BACKED by land rather than by air, so the ray enters material at the roof
    and never leaves, scoring one crossing and failing a drain that is more
    sealed than the one the check was written for.  Classify the roof directly
    instead -- it is the actual requirement, and it does not care what is
    stacked above.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON

    cls = BRepClass3d_SolidClassifier(closed.val().wrapped)
    cy = 0.5 * (pod.DRAIN_Y0 + pod.DRAIN_Y1)
    x = pod.DRAIN_X0 + 4.0
    solid = 0
    for dy in (-0.5, 0.0, 0.5):
        cls.Perform(gp_Pnt(x, cy + dy, pod.DRAIN_ROOF + 0.5), 1e-7)
        if cls.State() in (TopAbs_IN, TopAbs_ON):
            solid += 1
    # ...and the channel below it must still be open, or the drain drains
    # nothing.  Both halves of the labyrinth, tested where they actually are.
    open_ch = 0
    for dy in (-0.5, 0.0, 0.5):
        cls.Perform(gp_Pnt(x, cy + dy, pod.INNER_Z0 + 1.0), 1e-7)
        if cls.State() not in (TopAbs_IN, TopAbs_ON):
            open_ch += 1
    if solid < 3:
        r.fail(f"L8 no channel roof over the drain hole ({solid}/3 samples "
               f"solid at z={pod.DRAIN_ROOF + 0.5:.1f}) — it is a ram-air inlet")
    elif open_ch < 3:
        r.fail(f"L8 drain channel is blocked ({open_ch}/3 samples open at "
               f"z={pod.INNER_Z0 + 1.0:.1f}) — water cannot reach the outlet")
    else:
        r.ok(f"L8 labyrinth intact: roof over the outlet, channel open beneath")

def check_open_bay(r, pod, left, right) -> None:
    """I4 — the mating face must stay OPEN for install.

    Every existing check looks for missing material (holes, thin walls,
    detached features) or material in the wrong place (I2 protrusion).  None of
    them notice UNWANTED material sitting in the cavity, which is how a flange
    rail that filled the entire left cover with a 2.5 mm plate passed a green
    validator: watertight, single solid, nothing outside the envelope, no leak.

    Two tests, both physical:
      * the BOWL's bay must be empty aft of the SUN bulkhead.  This survived
        the clamshell inversion unchanged in substance but not in reason: it
        used to hold because pod_left was a bare cover, and it now holds
        because every board moved to the plate.  What hangs into the bowl
        there is the battery pocket (a recess, not material), the drain, and
        the boards' own components past the seam — no printed material.
      * the battery and the SUN must have clear insertion volume in BOTH
        halves, since they go in through the seam before close-up.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON

    cls = BRepClass3d_SolidClassifier(left.val().wrapped)
    blocked = []
    x0, x1 = pod.AFT_BH_X1 + 5.0, pod.FLANGE_X1 - 5.0
    for i in range(14):
        x = x0 + (x1 - x0) * i / 13
        for j in range(9):
            z = (pod.INNER_Z0 + 12.0) + ((pod.INNER_Z1 - 12.0) - (pod.INNER_Z0 + 12.0)) * j / 8
            # Follow the LOCAL inner skin: the boattail draws the left flank
            # inboard, so a constant LEFT_EXTENT samples inside the skin and
            # reports material that is simply the wall.
            y_in = pod.skin_y_minus(x, z, pod.WALL)
            for k in range(5):
                y = y_in + 0.8 + k * 0.9
                if y > -0.8:
                    continue
                cls.Perform(gp_Pnt(x, y, z), 1e-7)
                if cls.State() in (TopAbs_IN, TopAbs_ON):
                    blocked.append((x, y, z))
    if blocked:
        bx, by, bz = blocked[0]
        r.fail(f"I4 bowl bay obstructed at {len(blocked)} sample points, e.g. "
               f"({bx:.0f},{by:.1f},{bz:.0f}) — the bowl must be hollow between "
               "the SUN aft bulkhead and the tail rim")
    else:
        r.ok("I4 bowl bay clear for install")

    import cadquery as cq
    batt = (cq.Workplane("XY")
            .transformed(offset=(pod.BATT_X0, pod.BATT_Y0, pod.BATT_Z0))
            .box(pod.BATT_POCKET_X, pod.BATT_POCKET_Y, pod.BATT_POCKET_Z,
                 centered=(False, False, False)))
    # Stop short of the aft locating boss and use the smallest bore radius:
    # the boss is SUPPOSED to sit inside the SUN's aft cup, and the nose
    # bulkhead bore is tighter than the barrel, so neither is interference.
    # On PITOT_AXIS_Y, not y=0.  This was written when the seam ran down the
    # middle and the SUN sat on it; the inversion moved the pitot to y=-15 and
    # left the probe straddling the seam, where it clipped ordinary wall in
    # BOTH halves and reported 221/325 mm^3 of "fouling" that is simply the
    # pod.  A tool that models the part has to move when the part moves.
    sun = (cq.Workplane("XY")
           .transformed(offset=(0.0, pod.PITOT_AXIS_Y, pod.PITOT_AXIS_Z),
                        rotate=(0, 90, 0))
           .circle(pod.CRADLE_R_SMOOTH)
           .extrude(pod.AFT_BH_X0 - pod.SUN_RECESS_BOSS_LEN - 1.0))
    # The flange screw bosses legitimately survive inside the battery pocket —
    # add_battery_pocket protects them so the clamshell keeps its fasteners.
    for sx, sz in pod.FLANGE_SCREWS:
        if pod.BATT_X0 - pod.BOSS_D <= sx <= pod.BATT_X0 + pod.BATT_POCKET_X + pod.BOSS_D:
            batt = batt.cut(pod._cyl_y(sx, sz, -pod.FLANGE_W - 1.0,
                                       2 * pod.FLANGE_W + 2.0, pod.BOSS_D / 2))
    for name, half in (("pod_left", left), ("pod_right", right)):
        for what, tool in (("battery", batt), ("SUN barrel", sun)):
            v = half.val().intersect(tool.val()).Volume()
            if v > 50.0:
                r.fail(f"I4/I6 {what} insertion volume fouled in {name} by {v:.0f} mm^3")
            else:
                r.ok(f"I4 {what} clear in {name} ({v:.1f} mm^3)")


def check_insert_access(r, pod, right) -> None:
    """I7 — a heat-set iron must physically reach every insert pilot.

    For each seam-access insert, sweep a corridor of the boss diameter along
    the iron's path from the mating face to the pilot and require it empty.
    This is the check the old static bay would have failed: its -Y frame ran
    across the whole bay footprint at y 16.4..25.9, so the BMP581's pilots at
    y=28 had no corridor to them at all, and the fault was invisible to every
    solid check because the geometry was perfectly valid — just unbuildable.
    """
    import cadquery as cq

    blocked = []
    for ins in pod.insert_inventory():
        if ins["access"] != "seam":
            continue                      # aft inserts are entered from outside
        if ins["y"] <= 0.6:
            continue                      # flange pilots start at the seam itself
        corridor = pod._cyl_y(ins["x"], ins["z"], 0.5,
                              (ins["y"] - 0.2) - 0.5, pod.BOARD_POST_D / 2)
        v = right.val().intersect(corridor.val()).Volume()
        if v > 5.0:
            blocked.append((ins, v))
    if blocked:
        worst = sorted(blocked, key=lambda b: -b[1])[:4]
        detail = "; ".join(f"{i['name']} at ({i['x']:.0f},{i['z']:.0f}) by {v:.0f} mm^3"
                           for i, v in worst)
        r.fail(f"I7 heat-set iron cannot reach {len(blocked)} insert pilot(s): {detail}")
    else:
        n = sum(1 for i in pod.insert_inventory() if i["access"] == "seam")
        r.ok(f"I7 clear Ø{pod.BOARD_POST_D:.0f} corridor to all {n} seam-access "
             f"pilots (+{sum(1 for i in pod.insert_inventory() if i['access'] == 'aft')} "
             "entered from outside the base)")


def check_part_interference(r, pod, left, right) -> None:
    """I6 — every separately-printed part must fit where it is meant to sit.

    Each part is built in assembly position, so a straight intersection against
    both halves is the whole test.  pm_tray's retaining rim ran +Y into the
    wall land it bolts against and overlapped it by 1 mm; nothing caught that,
    because a part that is not in either half cannot foul either half until
    somebody checks.
    """
    for pname, part in pod.assembly_parts():
        for hname, half in (("pod_left", left), ("pod_right", right)):
            try:
                v = half.val().intersect(part.val()).Volume()
            except Exception:
                v = 0.0
            if v > 5.0:
                r.fail(f"I6 {pname} interferes with {hname} by {v:.0f} mm^3 "
                       "in assembly position")
            else:
                r.ok(f"I6 {pname} clears {hname} ({v:.1f} mm^3)")


def check_fastener_holes(r, pod, left, right) -> None:
    """P12 — every fastener hole must actually be open.

    I7 proves a tool can REACH a pilot; this proves the pilot exists.  They are
    different failures: the SUN clamp screws had a clear corridor and a visible
    boss, but add_pitot_cradle unioned the clamp land over them after the holes
    were cut, so there was no hole at all — solid PETG where the screw goes,
    and on the cover only a divot on the outer face.  Ordering, not geometry,
    and nothing looked wrong until the part was in hand.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    cr = BRepClass3d_SolidClassifier(right.val().wrapped)
    cl = BRepClass3d_SolidClassifier(left.val().wrapped)
    filled = []
    for ins in pod.insert_inventory():
        if ins["access"] == "seam":
            # the pilot itself, in the half that holds the insert
            probe = (ins["x"], ins["y"] + pod.INS_DEPTH * 0.5, ins["z"])
            cr.Perform(gp_Pnt(*probe), 1e-7)
            if cr.State() == TopAbs_IN:
                filled.append((ins["name"], "pilot", probe))
            if ins.get("through_left"):
                # and the clearance hole through the cover
                y_out = pod.section_params(ins["x"])[0]
                probe = (ins["x"], y_out * 0.5, ins["z"])
                cl.Perform(gp_Pnt(*probe), 1e-7)
                if cl.State() == TopAbs_IN:
                    filled.append((ins["name"], "cover clearance", probe))
        else:
            # aft-panel pilots sit in whichever half their y lies in
            probe = (pod.OUTER_L - pod.INS_DEPTH * 0.5, ins["y"], ins["z"])
            c = cr if ins["y"] > 0 else cl
            c.Perform(gp_Pnt(*probe), 1e-7)
            if c.State() == TopAbs_IN:
                filled.append((ins["name"], "pilot", probe))
    if filled:
        worst = "; ".join(f"{n} {what} at ({p[0]:.0f},{p[2]:.0f})" for n, what, p in filled[:5])
        r.fail(f"P12 {len(filled)} fastener hole(s) filled with material: {worst}"
               + ("" if len(filled) <= 5 else f" (+{len(filled) - 5} more)"))
    else:
        n = len(pod.insert_inventory())
        r.ok(f"P12 all {n} fastener holes open (pilots, plus cover clearance "
             "where the screw passes through)")


def _overlap(a, b):
    """3-D box overlap, or None."""
    o = [min(a[2 * i + 1], b[2 * i + 1]) - max(a[2 * i], b[2 * i]) for i in range(3)]
    return o if all(v > 0.05 for v in o) else None


def check_board_envelopes(r, pod) -> None:
    """I6' — a board's CONNECTORS need room, not just its outline.

    Pure arithmetic on boxes, so it runs in milliseconds and needs no solids.
    The board model had no way to express "a cable leaves here", which is the
    whole reason print-3 could not be wired: the MS4525's aft JST lands where
    the Boost has to go, and the Boost's forward Qwiic compounds it.  Both
    boards individually fitted their footprints perfectly.
    """
    names = list(pod.BOARDS)
    hits = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            for ea in pod.board_envelope(pod.BOARDS[names[i]]):
                for eb in pod.board_envelope(pod.BOARDS[names[j]]):
                    o = _overlap(ea, eb)
                    if o:
                        hits.append((names[i], names[j], o))
                        break
                else:
                    continue
                break
    # against the battery, and against the SUN and its cradle
    batt = (pod.BATT_X0, pod.BATT_X0 + pod.BATT_POCKET_X,
            pod.BATT_Y0, pod.BATT_Y0 + pod.BATT_POCKET_Y,
            pod.BATT_Z0, pod.BATT_Z0 + pod.BATT_POCKET_Z)
    for n in names:
        for e in pod.board_envelope(pod.BOARDS[n]):
            o = _overlap(e, batt)
            if o:
                hits.append((n, "battery", o))
                break
        for obs in pod.rail_obstacles(1):
            done = False
            for e in pod.board_envelope(pod.BOARDS[n]):
                o = _overlap(e, obs)
                if o:
                    hits.append((n, "flange rail", o))
                    done = True
                    break
            if done:
                break
        # the MS4525 is one END of the hose run, so it is exempt from it
        for obs in (pod.hose_obstacles() if n != "MS4525" else ()):
            done = False
            for e in pod.board_envelope(pod.BOARDS[n]):
                o = _overlap(e, obs)
                if o:
                    hits.append((n, "pitot/static hose run", o))
                    done = True
                    break
            if done:
                break
        for obs in (pod.cup_obstacles() if n != "BMP581" else ()):
            done = False
            for e in pod.board_envelope(pod.BOARDS[n]):
                o = _overlap(e, obs)
                if o:
                    hits.append((n, "static bay", o))
                    done = True
                    break
            if done:
                break
        for obs in pod.sun_obstacles():
            done = False
            for e in pod.board_envelope(pod.BOARDS[n]):
                o = _overlap(e, obs)
                if o:
                    hits.append((n, "SUN/cradle", o))
                    done = True
                    break
            if done:
                break
    if hits:
        detail = "; ".join(f"{a}/{b} by {o[0]:.1f}x{o[1]:.1f}x{o[2]:.1f}" for a, b, o in hits[:5])
        r.fail(f"I6' {len(hits)} connector-envelope clash(es): {detail}"
               + ("" if len(hits) <= 5 else f" (+{len(hits) - 5} more)"))
    else:
        r.ok(f"I6' all {len(names)} board envelopes clear each other, the battery, "
             "the SUN/cradle, the static bay and the hose run")


def check_no_invented_holes(r, pod) -> None:
    """I9 — every printed post traces to a measured feature.

    v2's wall-mount rework generated four corner standoffs for every board
    regardless of what the board had, which is how the MS4525 got two posts
    supporting nothing and the Pro Micro got four it has no holes for.  A post
    is legitimate only if it is a measured hole (screwed, or demoted to a nub
    because a heat-set iron cannot reach it) or a nub/keeper the caliper sheet
    explicitly calls for.
    """
    bad = []
    for n, b in pod.BOARDS.items():
        measured = {tuple(h) for h in b["holes"]}
        declared = {tuple(h) for h in b.get("nubs", [])}
        for u, v in pod.board_standoffs(b):
            if (u, v) not in measured:
                bad.append(f"{n} screw at ({u},{v}) is not a measured hole")
        for u, v in pod.board_nubs(b):
            if (u, v) not in measured | declared:
                bad.append(f"{n} nub at ({u},{v}) is neither measured nor declared")
        for u, v in pod.board_standoffs(b):
            if not pod._insert_reachable(b, v):
                bad.append(f"{n} insert at z={b['z0'] + v:.1f} is outside the "
                           f"reach band {pod.INSERT_Z_MIN:.1f}..{pod.INSERT_Z_MAX:.1f}")
    if bad:
        r.fail("I9 " + "; ".join(bad[:5]))
    else:
        n_s = sum(len(pod.board_standoffs(b)) for b in pod.BOARDS.values())
        n_n = sum(len(pod.board_nubs(b)) for b in pod.BOARDS.values())
        r.ok(f"I9 {n_s} inserts + {n_n} nubs, all traced to measured features")
