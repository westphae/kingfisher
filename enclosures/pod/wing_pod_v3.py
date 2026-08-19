#!/usr/bin/env python3
# =============================================================================
#  WING-MOUNTED AIR-DATA POD  v3  — left/right aerodynamic clamshell
#
#  What changed from v2 (and why)
#  ------------------------------
#  v2 built the outer mold line as  mid ∪ nose ∪ tail ∪ panel-pad  — four
#  solids fused by boolean.  Two whole classes of defect followed from that:
#
#    * The +Y panel pad was unioned at a fixed y (flush with the ellipse only
#      at the bottom of the rocker cut) and used the SAME x/z rectangle for the
#      outer body and the inner cavity.  outer.cut(inner) therefore left the
#      pad perimeter with ZERO wall: an ~80 × 3.5 mm slot straight into the
#      electronics bay under the flat top, plus open slots at both X ends.
#    * "No step at the nose/tail junction" (A2/A4/A5) held only by boolean
#      luck, and the tips had to be held off the seam (NOSE_TIP_YC = 0.35)
#      because an exact 0.0 broke the OCCT fuse.  BOTTOM_EDGE_R was disabled
#      outright because a post-hoc fillet would not fuse with the ogives.
#
#  v3 builds the whole OML as ONE loft over ONE parametric section family
#  (`section_wire`).  Nose, midbody and boattail are just stations of the same
#  family, driven by C1-continuous scale laws, so:
#
#    * inner and outer come from the same station list and the same wire
#      builder — a footprint mismatch like the v2 pad is unrepresentable;
#    * there are no junctions to step, so A2/A4/A5 hold by construction;
#    * BOTTOM_EDGE_R is section-native (a corner radius in the wire), which is
#      the fix the v2 comment deferred;
#    * the tips sit on the seam.
#
#  The service cluster (rocker / USB / LEDs) moved to the AFT FACE on a bolted,
#  gasketed plate.  That removes every cutout from the +Y skin, so the blister
#  that broke v2 has nowhere to come back.  It also puts the cuts in the base
#  wake — the lowest-pressure, lowest-impingement surface on the body.
#
#  A smooth loft OVERSHOOTS its control sections (measured: 0.077 mm past the
#  flat top).  The body is therefore intersected with an exact envelope box,
#  applied to inner and outer with matched insets so the wall stays constant.
#  A planar box intersection is the most robust boolean OCCT has; this is not
#  the same thing as fusing four lofted solids.
#
#  Boards (rearrangeable via BOARDS): SparkFun Pro Micro ESP32-C3, Battery
#  Babysitter (BQ27441), MMC5983MA, BMP581, Qwiic 5V Boost, Holybro MS4525DO,
#  LiPo 70 × 6 × 50 mm laid down (v2 stood it on its 70 mm edge, which is what
#  forced OUTER_H = 77 and left no length for a tail).
#
#  Air data (pneumatically decoupled):
#    Prandtl total  -> SUN-B aft barb -> 6 mm hose -> COTS reducer -> MS4525 +
#    Prandtl static -> SUN-B middle barb -> same path -> MS4525 -
#    SUN-B forward (TE) barb capped / unused
#    Isolated static bay (BMP581) -> BMP581; serviceable cover after heat-set
#
#  Pitot: ESA SUN-B (SUN_B_CALIPERS.md), protruding 45 mm and mounted on its
#  Ø11.76 threaded band — the feature the manufacturer put there for the job.
#  The Ø10.65→Ø11.76 step is the forward stop, seating on the nose bulkhead's
#  aft face.  That is a LARGER stop than v2's Ø8.93 shoulder, and it moves the
#  SUN aft face from x=103 to x=79, which is what pays for the boattail.
#
#  Printer: AnkerMake M5C (220 × 220 × 250).  See P0 in REQUIREMENTS.md:
#  halves print flange-down, rotated 45°, so OUTER_L + OUTER_H <= 297.
#  Re-run:  cd enclosures/pod && uv run --project .. python wing_pod_v3.py
# =============================================================================
from __future__ import annotations

import math

import cadquery as cq

# =============================================================================
# PARAMETERS (mm)
# =============================================================================

# --- shell / envelope -------------------------------------------------------
WALL = 2.5
OUTER_L = 235.0
LEFT_EXTENT = 10.0   # -Y cover side (battery half + wall + margin)
RIGHT_EXTENT = 42.0  # +Y electronics side
OUTER_W = LEFT_EXTENT + RIGHT_EXTENT
# 61, not 60: the flange rails intrude FLANGE_RAIL deep at the top and bottom
# near the seam, so the clear height a tall battery can occupy is
# OUTER_H - 2*(WALL + BATT_SEAL_KEEP) = 52 mm.  Going higher costs length via
# P0 and costs fineness twice (bigger D_eq, shorter L).
#
# The measured cell (49.3) would allow OUTER_H = 60 and buy fineness 3.71 ->
# 3.76.  Deliberately not taken: that spends the entire margin on the one
# binding dimension, and a pouch cell is the wrong thing to size to the last
# millimetre — it swells with age and charge, and a soft pouch is easy to
# under-read with calipers.  61 leaves 2.7 mm of spare instead of 0.7.
OUTER_H = 61.0       # flat bottom -> flat top

# Longitudinal breakdown.  NOSE_LEN and MID_END_X are the only two numbers
# that set the fairing proportions; everything aft of MID_END_X is boattail.
NOSE_LEN = 52.0
MID_END_X = 165.0
TAIL_LEN = OUTER_L - MID_END_X

# Section corner radii.  BOTTOM_EDGE_R is section-native (A7) — there is no
# post-hoc fillet to fail.  The top edge may be square (A6); a small radius
# keeps OCCT away from zero-length arcs and prints better than a knife edge.
BOTTOM_EDGE_R = 8.0
TOP_EDGE_R = 1.2
BASE_BOTTOM_R = 6.0
MIN_FLAT = 0.40  # shortest straight run kept on every side of every section

# Base (aft face).  Sized from the service cluster plus the insert rim, then
# checked against the boattail angle limit — see the A11/A12 asserts below.
# The taper split is EXPLICIT, not proportional to each side's extent: a
# proportional split hands ~12 mm of the 15 mm width reduction to the +Y side
# alone and pushes that surface to 13.2 deg (measured), past the A11 limit.
# Each of these three deltas is what A11 actually constrains.
# Sized FROM the service cluster, which is the correct dependency direction:
# at 37 x 52 the rocker and the LED pair fouled the opening and the USB ear
# nuts had 0.35 mm to the rim.  Enlarging the base also *reduces* the boattail
# angles (less taper over the same 70 mm) — the trade is base area, not angle.
BASE_Y0 = -5.0    # left cover gives up 5 mm -> 5.4 deg
BASE_Y1 = 36.0    # right side gives up 6 mm -> 6.5 deg
BASE_Z0 = 7.0     # bottom sweeps up 7 mm    -> 7.6 deg
BASE_W = BASE_Y1 - BASE_Y0
BASE_H = OUTER_H - BASE_Z0

# Loft station counts and clustering.  The section laws' curvature peaks at the
# END of the nose (|f''| = 4.22 at t=1 vs 2.44 at t=0) and at the END of the
# boattail, so stations cluster toward those ends.  Measured max chord error
# between the ruled surface and the true law: 0.037 mm at 49 stations with this
# distribution, vs 0.036 mm at 75 stations clustered the other way — a third
# fewer faces for the same accuracy, and every boolean downstream is cheaper.
N_NOSE, N_MID, N_TAIL = 22, 8, 18
STATION_ERR_MAX = 0.05  # mm; asserted below

# --- clamshell / flange -----------------------------------------------------
FLANGE_W = 6.0        # mating-face flange depth into each half (±Y)
FLANGE_RAIL = 4.0     # perimeter rail on the open mating face (I4)
GASKET_W = 1.6
GASKET_D = 0.9
SHELL_SCREW_INSET = 3.0
FLANGE_SCREW_PITCH = 28.0
# (x, z) of the two screws that clamp the SUN's threaded band.  z sits above
# the bore: below it the section bottom runs out before the land does.
CLAMP_SCREWS = [(13.0, 34.0), (27.0, 34.0)]

# --- M2.5 heat-set inserts (same family as pi5_aviation_case.py) ------------
INSERT_OD = 3.47
INSERT_LEN = 3.98
MELT_ALLOWANCE = 0.30
HOLE_DEPTH_EXTRA = 0.50
INS_HOLE_D = INSERT_OD - MELT_ALLOWANCE
INS_DEPTH = INSERT_LEN + HOLE_DEPTH_EXTRA
SCREW_RELIEF_D = 2.9
SCREW_RELIEF_EXTRA = 4.0
SCREW_RELIEF_FLOOR = 1.0
LID_SCREW_D = 2.8
LID_CB_D = 5.0
LID_CB_DEPTH = 2.2
BOSS_D = 7.0
BOARD_POST_D = 7.0
BOARD_POST_H = INS_DEPTH + SCREW_RELIEF_EXTRA + SCREW_RELIEF_FLOOR + 1.0

# --- ESA SUN-B pitot mount (calipers: SUN_B_CALIPERS.md, 2026-08-09) --------
SUN_TIP_OD = 8.93
SUN_SMOOTH_OD = 10.65
SUN_THREAD_MAJOR = 11.76
SUN_BARREL_OD = 11.71
SUN_TIP_LEN = 24.75
SUN_SMOOTH_LEN = 27.50
SUN_THREAD_LEN = 25.37
SUN_TOTAL_LEN = 124.03
SUN_BARB_TE_FROM_NOSE = 85.0
SUN_BARB_STATIC_FROM_NOSE = 100.0
SUN_BARB_PITOT_FROM_NOSE = 114.0
SUN_BARB_STEM_OD = 5.96
SUN_BARB_ABOVE_BARREL = 16.08
SUN_RECESS_D = 6.03
SUN_RECESS_DEPTH = 7.06
SUN_RECESS_BOSS_AXIAL_CLR = 2.50
SUN_RECESS_BOSS_DIA_CLR = 0.40
# Print-1 (PETG split half-pipes): 0.20 mm radial slip was OK on the aft
# barrel but tight on the smooth barrel; the clamp at 0.08 was far too small.
CRADLE_CLEAR_SMOOTH = 0.40
CRADLE_CLEAR_BARREL = 0.22
CLAMP_CLEAR = 0.28
# v3: SUN protrudes 45 mm and is stopped by the Ø10.65 -> Ø11.76 step.
SUN_PROTRUDE = 45.0
SHOULDER_BH_T = 3.5   # nose bulkhead thickness (print-1: 2.5 tore at the lip)
NOSE_LIP_WALL = 1.60  # print-1: a 0.7 mm lip tore off the left half
# The mouth is centred on this axis, so it also decides how lopsided the nose
# is.  At 18 the top surface had to climb 36 mm in 52 (~35 deg) while the
# bottom fell only 10.  At 24 the climb is ~30 mm and the drop ~17, which is a
# far more balanced fairing, and the barbs still clear the ceiling with margin.
PITOT_AXIS_Z = 24.0
MS_TUBE_OD = 3.5      # silicone OD over 3/32" ID line (VERIFY)
MS_BARB_TIP_D = 2.1
MS_BARB_SHOULDER_D = 3.5
MS_BARB_DY = 4.3

# --- battery (slab on the seam, LAID DOWN in v3) ----------------------------
# Measured 2026-08-18 (BATTERY_CALIPERS.md), leads on the aft edge as assumed.
# All three came in under the spec figures, so nothing had to move.
BATT_X = 68.5
BATT_Y = 5.9
BATT_Z = 49.3
BATT_CLR = 1.0
# Z clearance is tighter than X/Y on purpose: the foam tape goes on the Y faces,
# and Z is the axis fighting the flange rails (see OUTER_H).
BATT_CLR_Z = 0.5
# The pocket may never reach the sealing land.  Keeping it this far inside the
# outer skin leaves WALL + BATT_SEAL_KEEP of continuous flange for the O-cord,
# so the battery notches only the inner part of the rail.
BATT_SEAL_KEEP = 2.0
BATT_POCKET_X = BATT_X + 2 * BATT_CLR
BATT_POCKET_Y = BATT_Y + 2 * BATT_CLR
BATT_POCKET_Z = BATT_Z + 2 * BATT_CLR_Z

# --- boards (wall-mount on the +Y inner skin) -------------------------------
PCB_T = 1.6
COMP_H = 8.0
MS_COMP_H = 12.0
WALL_LAND_T = 7.0
STANDOFF_H = 4.0
BOARD_GAP = 4.0
INSET = 2.54
BMP581_L, BMP581_W = 25.4, 25.4
BOOST_L, BOOST_W = 24.5, 24.5
PM_L, PM_W = 33.0, 17.8   # v3: long axis along X so it stacks over the Boost
MAG_L, MAG_W = 19.0, 7.6
MS_L, MS_W = 22.9, 17.0
BABY_L, BABY_W = 33.0, 33.0
Y_LAND = 32.0
Y_PCB = Y_LAND - STANDOFF_H

# --- isolated static bay (S3) ----------------------------------------------
# The plenum is a SEPARATE cup that seals to the wall land, not walls moulded
# into the right half with a service window through them.  With integral walls
# the window has to be big enough to pass a heat-set iron yet small enough to
# leave a sealing frame, and those two demands do not both fit: all four BMP
# bosses ended up behind the frame, and the cover screws landed inside the
# board footprint.  A cup leaves the BMP standing on a completely open wall.
CUP_WALL = 2.0
CUP_CLR = 2.0       # plenum clearance around the BMP board
CUP_TAB = 3.0       # screw tab reach beyond the cup wall
CUP_FLANGE = 2.5    # flange thickness where it seals to the land
STATIC_COVER_T = 2.2
STATIC_HOLE_D = 1.6
STATIC_HOLE_ROWS = 2
STATIC_HOLE_COLS = 5
STATIC_HOLE_PITCH_X = 4.5
STATIC_HOLE_PITCH_Z = 4.5

# --- printer ----------------------------------------------------------------
BED = 220.0
BED_Z = 250.0
BED_MARGIN = 10.0
BED_LIMIT = BED - BED_MARGIN
# Skirt/brim and support outlines are drawn OUTSIDE the object, so the part
# must be centred with room around it — parking it on the origin corner puts
# those toolpaths at negative coordinates and the slicer rejects the job.
BRIM_ALLOWANCE = 8.0

# =============================================================================
# DERIVED LAYOUT  (x = 0 at the outer nose tip, +X aft, y = 0 at the seam)
# =============================================================================
INNER_H = OUTER_H - 2 * WALL
INNER_Z0 = WALL
INNER_Z1 = OUTER_H - WALL
HALF_W = RIGHT_EXTENT


def sun_x(station_from_nose: float) -> float:
    """Pod X of a SUN-B station measured from the SUN's own nose tip."""
    return station_from_nose - SUN_PROTRUDE


SUN_TIP_X0 = sun_x(0.0)
SUN_SMOOTH_X0 = sun_x(SUN_TIP_LEN)
SUN_THREAD_X0 = sun_x(SUN_TIP_LEN + SUN_SMOOTH_LEN)
SUN_THREAD_X1 = sun_x(SUN_TIP_LEN + SUN_SMOOTH_LEN + SUN_THREAD_LEN)
SUN_BARREL_X0 = SUN_THREAD_X1
SUN_AFT_X = sun_x(SUN_TOTAL_LEN)
SUN_BARB_X = [
    sun_x(SUN_BARB_TE_FROM_NOSE),
    sun_x(SUN_BARB_STATIC_FROM_NOSE),
    sun_x(SUN_BARB_PITOT_FROM_NOSE),
]
SUN_BARB_TIP_Z = PITOT_AXIS_Z + SUN_BARREL_OD / 2 + SUN_BARB_ABOVE_BARREL

CRADLE_R_SMOOTH = SUN_SMOOTH_OD / 2 + CRADLE_CLEAR_SMOOTH
CRADLE_R_CLAMP = SUN_THREAD_MAJOR / 2 + CLAMP_CLEAR
CRADLE_R_BARREL = SUN_BARREL_OD / 2 + CRADLE_CLEAR_BARREL
# +7, not +5: the clamp needs screws through it to actually cinch the halves
# onto the thread (print-2: the nose spread slightly at the press fit).  At
# +5 there was exactly one workable screw height, with 6.3 mm of land and
# 1.5 mm from pilot to bore.  At +7 the land is capped by CRADLE_LAND_Y and a
# screw at z=34 gets 8.6 mm of land and 2.3 mm of bore clearance.
CLAMP_R_OUTER = CRADLE_R_CLAMP + 7.0
CRADLE_LAND_Y = CRADLE_R_CLAMP + 7.0
NOSE_MOUTH_R = CRADLE_R_SMOOTH + NOSE_LIP_WALL

# Nose bulkhead: the Ø10.65 -> Ø11.76 step butts against its AFT face, so the
# bulkhead sits forward of the thread and its bore passes the smooth barrel.
NOSE_BH_X1 = SUN_THREAD_X0
NOSE_BH_X0 = NOSE_BH_X1 - SHOULDER_BH_T
# Aft bulkhead carries the locating boss into the Ø6.03 x 7.06 blind cup.
AFT_BH_X0 = SUN_AFT_X
AFT_BH_X1 = AFT_BH_X0 + SHOULDER_BH_T
SUN_RECESS_BOSS_LEN = SUN_RECESS_DEPTH - SUN_RECESS_BOSS_AXIAL_CLR
SUN_RECESS_BOSS_D = SUN_RECESS_D - SUN_RECESS_BOSS_DIA_CLR

# --- board columns on the +Y wall -------------------------------------------
# Two rows in Z wherever the pair fits the 55 mm interior; the Babysitter and
# the static bay are tall enough to need a column each.
# I7: the bottom flange rail fills z up to INNER_Z0 + FLANGE_RAIL at y 0..6,
# so an insert any lower than this has the rail inside the heat-set iron's
# corridor for the first 6 mm of its travel.  This is what sets the low row.
INSERT_Z_MIN = INNER_Z0 + FLANGE_RAIL + BOARD_POST_D / 2 + 0.5
INSERT_Z_MAX = INNER_Z1 - FLANGE_RAIL - BOARD_POST_D / 2 - 0.5
LOW_ROW_Z0 = 8.5                                   # -> lower pilots at z=11.0

COL_A_X0 = 44.0                                    # MS4525 low + MMC5983 high
MS_X0, MS_Z0 = COL_A_X0, LOW_ROW_Z0
MAG_X0, MAG_Z0 = COL_A_X0, 30.0
COL_A_W = max(MS_L, MAG_L)

COL_B_X0 = COL_A_X0 + COL_A_W + BOARD_GAP          # Boost low + Pro Micro high
BOOST_X0, BOOST_Z0 = COL_B_X0, LOW_ROW_Z0
PM_X0, PM_Z0 = COL_B_X0, 35.5
COL_B_W = max(BOOST_L, PM_L)

CUP_X0 = COL_B_X0 + COL_B_W + BOARD_GAP            # isolated static bay (cup)
# Centred in the band the flange rails leave: with a 29.4 mm plenum and
# 3 mm tabs the cup spans 39.4 mm against 40.0 mm of usable height, so
# there is exactly one place it can sit.
BMP_X0, BMP_Z0 = CUP_X0 + CUP_WALL + CUP_CLR, 17.8
# BAY_* is the PLENUM volume (cup interior); CUP_* is the cup's outer shell.
BAY_X0, BAY_X1 = BMP_X0 - CUP_CLR, BMP_X0 + BMP581_L + CUP_CLR
BAY_Z0, BAY_Z1 = BMP_Z0 - CUP_CLR, BMP_Z0 + BMP581_W + CUP_CLR
CUP_X1 = BAY_X1 + CUP_WALL
CUP_Z0, CUP_Z1 = BAY_Z0 - CUP_WALL, BAY_Z1 + CUP_WALL
CUP_Y0 = Y_PCB - PCB_T - COMP_H - 3.0              # inner face, 1 mm over the BMP
BAY_Y0 = CUP_Y0

BABY_X0, BABY_Z0 = CUP_X1 + BOARD_GAP, 12.0        # Babysitter, full column

# Battery: laid down on the seam, clear of the SUN aft bulkhead.
BATT_X0 = AFT_BH_X1 + 2.5
BATT_Y0 = -BATT_POCKET_Y / 2
BATT_Z0 = (OUTER_H - BATT_POCKET_Z) / 2

# Static ports sit mid-body, where a side port reads closest to freestream.
STATIC_PORT_X = 0.5 * (BAY_X0 + BAY_X1)
STATIC_PORT_Z = BMP_Z0 + BMP581_W / 2

# Flange runs from just aft of the nose fairing to just short of the base.
# It must continue through the boattail: without it the two halves are held
# only forward of the taper and the last ~60 mm would be unfastened.
FLANGE_X0 = NOSE_LEN + 2.0
FLANGE_X1 = OUTER_L - 6.0

# =============================================================================
# SECTION FAMILY  — the single source of the whole outer mold line
# =============================================================================
# A section is (y0, y1, z0, z1, r00, r10, r11, r01) where rIJ is the corner
# radius at (y_I, z_J).  Every station is built by the same code, and the
# inner cavity is the same call with inset=WALL.  That equality is the whole
# point: it is what makes a v2-style inner/outer footprint mismatch impossible.
MID_SEC = (
    -LEFT_EXTENT, RIGHT_EXTENT, 0.0, OUTER_H,
    BOTTOM_EDGE_R, BOTTOM_EDGE_R, TOP_EDGE_R, TOP_EDGE_R,
)
_MR = NOSE_MOUTH_R
MOUTH_SEC = (
    -_MR, _MR, PITOT_AXIS_Z - _MR, PITOT_AXIS_Z + _MR,
    _MR - MIN_FLAT / 2, _MR - MIN_FLAT / 2, _MR - MIN_FLAT / 2, _MR - MIN_FLAT / 2,
)
BASE_SEC = (
    BASE_Y0, BASE_Y1, BASE_Z0, OUTER_H,
    BASE_BOTTOM_R, BASE_BOTTOM_R, TOP_EDGE_R, TOP_EDGE_R,
)


def _nose_law(t: float, m0: float = 0.89) -> float:
    """Nose growth: f(0)=0, f(1)=1, f'(1)=0, f'(0)=m0.  Monotone on [0,1].

    f'(1)=0 is what makes the join to the constant midbody C1, so there is no
    shoulder kink to detect — A2/A4/A5 are satisfied by the law, not by luck.
    Front-loaded (m0>0) like an elliptical nose, which is the low-drag shape
    for a subsonic body of revolution.
    """
    a, b, c = m0, 3.0 - 2.0 * m0, m0 - 2.0
    return a * t + b * t * t + c * t ** 3


def _tail_law(u: float, m1: float = 1.2) -> float:
    """Boattail: g(0)=0, g'(0)=0, g(1)=1, g'(1)=m1.  Monotone on [0,1].

    g'(0)=0 gives a kink-free shoulder; g'(1)=m1>0 leaves the surface still
    converging at the base so the flow turns inward rather than dumping off a
    parallel rim.
    """
    b, c = 3.0 - m1, m1 - 2.0
    return b * u * u + c * u ** 3


def section_params(x: float) -> tuple[float, ...]:
    """The 8-tuple describing the outer section at station x."""
    if x <= NOSE_LEN:
        f = _nose_law(max(0.0, x) / NOSE_LEN)
        return tuple(a + (b - a) * f for a, b in zip(MOUTH_SEC, MID_SEC))
    if x <= MID_END_X:
        return MID_SEC
    g = _tail_law(min(1.0, (x - MID_END_X) / TAIL_LEN))
    return tuple(a + (b - a) * g for a, b in zip(MID_SEC, BASE_SEC))


def section_wire(x: float, inset: float = 0.0) -> cq.Wire:
    """Closed YZ wire at station x, offset inward by `inset`.

    Identical construction at every station (4 lines + 4 arcs, same order), so
    the loft never has to reconcile differing topologies.
    """
    y0, y1, z0, z1, r00, r10, r11, r01 = section_params(x)
    y0, y1, z0, z1 = y0 + inset, y1 - inset, z0 + inset, z1 - inset
    rs = [max(r - inset, 0.30) for r in (r00, r10, r11, r01)]
    w, h = y1 - y0, z1 - z0
    # Never let opposing radii consume a whole side: keep MIN_FLAT of straight.
    sc = min(
        1.0,
        (w - MIN_FLAT) / max(rs[0] + rs[1], 1e-9),
        (w - MIN_FLAT) / max(rs[3] + rs[2], 1e-9),
        (h - MIN_FLAT) / max(rs[0] + rs[3], 1e-9),
        (h - MIN_FLAT) / max(rs[1] + rs[2], 1e-9),
    )
    r00, r10, r11, r01 = (r * sc for r in rs)

    def P(y: float, z: float) -> cq.Vector:
        return cq.Vector(x, y, z)

    def arc(p: tuple[float, float], q: tuple[float, float],
            c: tuple[float, float], r: float) -> cq.Edge:
        vy, vz = p[0] - c[0], p[1] - c[1]
        wy, wz = q[0] - c[0], q[1] - c[1]
        my, mz = vy + wy, vz + wz
        n = math.hypot(my, mz)
        mid = (c[0] + r * my / n, c[1] + r * mz / n)
        return cq.Edge.makeThreePointArc(P(*p), P(*mid), P(*q))

    bl, br = (y0 + r00, z0 + r00), (y1 - r10, z0 + r10)
    tr, tl = (y1 - r11, z1 - r11), (y0 + r01, z1 - r01)
    edges = [
        cq.Edge.makeLine(P(y0 + r00, z0), P(y1 - r10, z0)),
        arc((y1 - r10, z0), (y1, z0 + r10), br, r10),
        cq.Edge.makeLine(P(y1, z0 + r10), P(y1, z1 - r11)),
        arc((y1, z1 - r11), (y1 - r11, z1), tr, r11),
        cq.Edge.makeLine(P(y1 - r11, z1), P(y0 + r01, z1)),
        arc((y0 + r01, z1), (y0, z1 - r01), tl, r01),
        cq.Edge.makeLine(P(y0, z1 - r01), P(y0, z0 + r00)),
        arc((y0, z0 + r00), (y0 + r00, z0), bl, r00),
    ]
    return cq.Wire.assembleEdges(edges)


def stations() -> list[float]:
    """Loft stations, clustered where the section is changing fastest."""
    xs = [NOSE_LEN * (1.0 - (1.0 - i / N_NOSE) ** 1.35) for i in range(N_NOSE)]
    xs += [NOSE_LEN + (MID_END_X - NOSE_LEN) * i / N_MID for i in range(N_MID)]
    xs += [MID_END_X + TAIL_LEN * (1.0 - (1.0 - i / N_TAIL) ** 1.2)
           for i in range(N_TAIL + 1)]
    return sorted({round(v, 6) for v in xs})


_STATIONS = stations()


def station_chord_error() -> tuple[float, float]:
    """How far the ruled loft departs from the true section law between
    stations.  Cheap, exact, and the honest way to justify the station count."""
    worst, at = 0.0, 0.0
    for a, b in zip(_STATIONS, _STATIONS[1:]):
        pa, pb = section_params(a), section_params(b)
        for t in (0.25, 0.5, 0.75):
            pm = section_params(a + (b - a) * t)
            for k in range(4):
                e = abs(pm[k] - (pa[k] + (pb[k] - pa[k]) * t))
                if e > worst:
                    worst, at = e, a + (b - a) * t
    return worst, at


_ERR, _ERR_X = station_chord_error()
assert _ERR <= STATION_ERR_MAX, (
    f"loft faceting {_ERR:.4f} mm at x={_ERR_X:.1f} exceeds {STATION_ERR_MAX} — "
    "raise N_NOSE / N_MID / N_TAIL"
)
_ENVELOPE_CACHE: dict[float, cq.Solid] = {}


MIN_SECTION = 2.0  # smallest width/height an inset section may retain


def _section_ok(x: float, inset: float) -> bool:
    """Does the section still have real area once inset by `inset`?"""
    y0, y1, z0, z1 = section_params(x)[:4]
    return ((y1 - inset) - (y0 + inset) > MIN_SECTION
            and (z1 - inset) - (z0 + inset) > MIN_SECTION)


def full_body_solid(inset: float = 0.0) -> cq.Workplane:
    """The whole OML as ONE loft — nose, midbody and boattail are stations of
    the same section family, not separate solids fused together.

    ruled=True is deliberate.  A smooth (ruled=False) loft overshoots its own
    control sections — measured 0.028 mm past the flat top at inset 0 and
    0.028 mm past the inner floor at inset WALL — which leaves the wing-mate
    face non-planar (zero planar faces at z=OUTER_H) and makes the envelope
    oversize.  Correcting that with an envelope box then fails: the corrected
    inner loft is tangent to the box over whole faces and OCCT's intersect
    returns an empty shape.  A ruled loft stays inside the convex hull of
    adjacent sections, so the flats come out exactly planar, the extents are
    exact, and no correction boolean is needed at all.  The cost is 594 faces
    instead of 10; the facet chord error between stations is well under a
    printed layer.
    """
    key = round(inset, 6)
    if key not in _ENVELOPE_CACHE:
        # Drop stations where the inset would invert the section.  The nose
        # mouth is only 2 x NOSE_MOUTH_R across, so any inset past that turns
        # the wire inside out and makeLoft returns a solid with NEGATIVE
        # volume.  Cutting with such a solid ADDS material: at inset 7.5 the
        # tail rim's _rail_shell came out at 763 cm^3 from a 522 cm^3 body and
        # dumped a 2.5 mm plate down the whole seam of the left cover.  An
        # inset body legitimately does not reach the mouth; truncating is the
        # correct geometry, not a workaround.
        xs = [x for x in _STATIONS if _section_ok(x, inset)]
        assert len(xs) >= 2, (
            f"full_body_solid(inset={inset}): the section collapses at every "
            "station — inset is larger than the body"
        )
        # ruled=False.  A ruled loft creases at every station: measured 4.28 deg
        # between adjacent patches at the nose and 1.06 deg in the boattail,
        # at 0.8..4.6 mm spacing, which prints as washboard on the sloped
        # surfaces.  A smooth loft has no creases but overshoots its control
        # sections by ~0.025 mm, so the flats are trimmed back with half-space
        # CUTS.  Cuts work where the 6-face envelope intersect did not: that
        # failed outright at inset=WALL because the corrected solid was tangent
        # to the box over whole faces.  Bonus: 30 faces instead of 386, so
        # every downstream boolean is cheaper.
        loft = cq.Solid.makeLoft([section_wire(x, inset) for x in xs], ruled=False)
        big = 3 * OUTER_H
        above = cq.Solid.makeBox(OUTER_L + 8, 4 * OUTER_W, big,
                                 cq.Vector(-4, -2 * OUTER_W, OUTER_H - inset))
        below = cq.Solid.makeBox(OUTER_L + 8, 4 * OUTER_W, big,
                                 cq.Vector(-4, -2 * OUTER_W, inset - big))
        solid = loft.cut(above).cut(below)
        # .cut() hands back a Compound; unwrap so downstream code sees a Solid.
        _sol = solid.Solids()
        if len(_sol) == 1:
            solid = _sol[0]
        vol = solid.Volume()
        assert vol > 0.0, (
            f"full_body_solid(inset={inset}) has volume {vol / 1000:.1f} cm^3. "
            "A negative volume means the loft is inverted; any cut against it "
            "will add material instead of removing it."
        )
        _ENVELOPE_CACHE[key] = solid
    return cq.Workplane("XY").newObject([_ENVELOPE_CACHE[key]])


def _assert_exact_envelope() -> None:
    """The flats are mating and standing surfaces (A6/A7) — verify the loft
    really lands on them rather than trusting ruled=True."""
    for inset in (0.0, WALL):
        x0, x1, y0, y1, z0, z1 = mesh_extents(full_body_solid(inset))
        for got, want, name in (
            (z0, inset, "z0"), (z1, OUTER_H - inset, "z1"),
            (y0, -LEFT_EXTENT + inset, "y0"), (y1, RIGHT_EXTENT - inset, "y1"),
        ):
            assert abs(got - want) < 0.05, (
                f"envelope inset={inset}: {name} = {got:.4f}, expected {want:.4f}"
            )


def skin_y_plus(x: float, z: float, inset: float = 0.0) -> float:
    """+Y coordinate of the section boundary at (x, z).  Exact for the
    rounded-rect family, so board / land / port checks use the real skin
    rather than an ellipse approximation."""
    y0, y1, z0, z1, r00, r10, r11, r01 = section_params(x)
    y0, y1, z0, z1 = y0 + inset, y1 - inset, z0 + inset, z1 - inset
    r10, r11 = max(r10 - inset, 0.30), max(r11 - inset, 0.30)
    if z <= z0 or z >= z1:
        return y1 - min(r10, r11)
    if z < z0 + r10:
        return (y1 - r10) + math.sqrt(max(0.0, r10 ** 2 - (z0 + r10 - z) ** 2))
    if z > z1 - r11:
        return (y1 - r11) + math.sqrt(max(0.0, r11 ** 2 - (z - (z1 - r11)) ** 2))
    return y1


def skin_y_minus(x: float, z: float, inset: float = 0.0) -> float:
    """-Y coordinate of the section boundary at (x, z).  Mirror of
    skin_y_plus, needed wherever a check must follow the left flank as the
    boattail draws it inboard rather than assuming a constant LEFT_EXTENT."""
    y0, y1, z0, z1, r00, r10, r11, r01 = section_params(x)
    y0, z0, z1 = y0 + inset, z0 + inset, z1 - inset
    r00, r01 = max(r00 - inset, 0.30), max(r01 - inset, 0.30)
    if z <= z0 or z >= z1:
        return y0 + min(r00, r01)
    if z < z0 + r00:
        return (y0 + r00) - math.sqrt(max(0.0, r00 ** 2 - (z0 + r00 - z) ** 2))
    if z > z1 - r01:
        return (y0 + r01) - math.sqrt(max(0.0, r01 ** 2 - (z - (z1 - r01)) ** 2))
    return y0


def frontal_area() -> float:
    """Max cross-section area, corner radii deducted."""
    y0, y1, z0, z1, r00, r10, r11, r01 = MID_SEC
    box = (y1 - y0) * (z1 - z0)
    return box - sum(r ** 2 * (1 - math.pi / 4) for r in (r00, r10, r11, r01))


def base_area() -> float:
    y0, y1, z0, z1, r00, r10, r11, r01 = BASE_SEC
    box = (y1 - y0) * (z1 - z0)
    return box - sum(r ** 2 * (1 - math.pi / 4) for r in (r00, r10, r11, r01))


def max_nose_angle() -> float:
    """Steepest OML surface angle over the nose fairing.

    Reported, and gated far more loosely than the boattail, because the two are
    not comparable: the nose runs a favourable pressure gradient (flow
    accelerating, boundary layer thin and attached) while the boattail runs an
    adverse one where the same angle would separate.  A hemispherical nose is
    90 deg at the tip.  What actually sets this number is that a 52 mm nose has
    to grow from a Ø14.6 mouth to a 52 mm body, and the mouth cannot move off
    the seam because the cradle is split L/R.
    """
    worst = 0.0
    n = 800
    for i in range(n):
        x = NOSE_LEN * i / (n - 1)
        a = section_params(x)
        b = section_params(min(x + 0.05, NOSE_LEN))
        for k in (0, 1, 2, 3):
            worst = max(worst, abs(b[k] - a[k]) / 0.05)
    return math.degrees(math.atan(worst))


def max_boattail_angle() -> float:
    """Steepest OML surface angle to the freestream aft of max thickness."""
    worst = 0.0
    n = 800
    for i in range(n):
        x = MID_END_X + TAIL_LEN * i / (n - 1)
        a = section_params(x)
        b = section_params(min(x + 0.05, OUTER_L))
        for k in (0, 1, 2):
            worst = max(worst, abs(b[k] - a[k]) / 0.05)
    return math.degrees(math.atan(worst))


FRONTAL_A = frontal_area()
BASE_A = base_area()
D_EQ = math.sqrt(4.0 * FRONTAL_A / math.pi)
FINENESS = OUTER_L / D_EQ
BOATTAIL_ANGLE = max_boattail_angle()
NOSE_ANGLE = max_nose_angle()

# --- non-negotiables, asserted at import ------------------------------------
# P0: halves print flange-down and rotated 45°, so the bed diagonal governs.
assert OUTER_L + OUTER_H <= BED_LIMIT * math.sqrt(2.0) + 1e-6, (
    f"P0 OUTER_L+OUTER_H = {OUTER_L + OUTER_H:.1f} > {BED_LIMIT * math.sqrt(2.0):.1f}"
)
assert RIGHT_EXTENT + FLANGE_W <= BED_Z, "P0 print height exceeds the M5C Z travel"
# A11/A12: aero targets, stated as numbers so they cannot quietly regress.
assert BOATTAIL_ANGLE <= 12.0, f"A11 boattail angle {BOATTAIL_ANGLE:.1f}deg > 12"
# 0.72: the measured cost of putting the service cluster on the aft face.  The
# cluster needs ~26 x 39 mm of clear opening, and the opening plus wall plus
# insert rim is what sets the base.  v2's effective base ratio was ~0.95.
assert BASE_A / FRONTAL_A <= 0.72, f"A12 base/frontal {BASE_A / FRONTAL_A:.2f} > 0.72"
# 3.75 is what the payload allows, not an aspiration met: OUTER_H is set by
# the 50 mm battery pocket and OUTER_W by the board land, so the section
# cannot shrink further without moving hardware.  v2 was 3.4 WITH a 12 mm
# tail; the gain here is almost entirely in the boattail, not the slenderness.
assert FINENESS >= 3.70, f"A12 fineness {FINENESS:.2f} < 3.70"
assert NOSE_LEN >= 0.8 * D_EQ, "A3 nose fairing shorter than 0.8 diameters"
assert NOSE_ANGLE <= 45.0, f"A13 nose surface angle {NOSE_ANGLE:.1f}deg > 45"
assert TAIL_LEN >= 1.1 * D_EQ, "A3 boattail shorter than 1.1 diameters"
# P7 / L2: print-1 lessons.
assert NOSE_LIP_WALL >= 1.5, "P7 nose lip below the 1.5 mm that survived print-1"
assert CLAMP_CLEAR >= 0.20, "L2 clamp clearance below the print-1 slip fit"
assert SUN_RECESS_DEPTH - SUN_RECESS_BOSS_LEN >= 2.5, (
    "P5 locating boss must leave >=2.5 mm of the cup unused"
)
assert SUN_BARB_TIP_Z + 8.0 < INNER_Z1, (
    f"L3 barb tips z={SUN_BARB_TIP_Z:.1f} leave no hose room under z={INNER_Z1:.1f}"
)
# I6: the MS4525's barbs+reducer hang inboard of its PCB and must clear the
# pitot cradle land, which is the widest thing on the centreline.
assert Y_PCB - PCB_T - MS_COMP_H >= CRADLE_LAND_Y + 1.0, (
    f"I6 MS4525 keepout reaches y={Y_PCB - PCB_T - MS_COMP_H:.1f}, cradle land is "
    f"{CRADLE_LAND_Y:.1f} — raise Y_LAND or shorten the standoffs"
)
# L1: the battery pocket may notch interior material only.
assert BATT_Z0 >= INNER_Z0 + 0.5 and BATT_Z0 + BATT_POCKET_Z <= INNER_Z1 - 0.5, (
    "L1 battery pocket does not fit the interior height"
)
assert BATT_X0 >= AFT_BH_X1, "L1 battery pocket overlaps the SUN aft bulkhead"
# P6/W1: material left behind the left-cover counterbore at the shallowest
# screw station.  A counterbore sunk into bare wall leaves 0.3 mm and tears out.
assert LEFT_EXTENT - LID_CB_DEPTH >= 1.5, (
    f"P6 left counterbore {LID_CB_DEPTH:.1f} deep leaves only "
    f"{LEFT_EXTENT - LID_CB_DEPTH:.1f} mm of boss behind it"
)
# L1/S2: the pack is a tall slab on the seam and the flange rails run along the
# top and bottom there.  This is the clearance that actually binds OUTER_H.
_BATT_CLEAR_Z = OUTER_H - 2 * (WALL + BATT_SEAL_KEEP)
assert BATT_POCKET_Z <= _BATT_CLEAR_Z - 0.5, (
    f"L1 battery pocket {BATT_POCKET_Z:.1f} mm tall vs {_BATT_CLEAR_Z:.1f} mm clear "
    f"between the flange rails — raise OUTER_H or trim BATT_CLR_Z"
)
assert GASKET_W / 2.0 + GASKET_D <= BATT_SEAL_KEEP, (
    "S2 gasket groove would be inside the region the battery pocket may notch"
)

# --- board table ------------------------------------------------------------
BOARDS: dict[str, dict] = {
    "MS4525": dict(x0=MS_X0, z0=MS_Z0, L=MS_L, W=MS_W, comp=MS_COMP_H,
                   holes=[(INSET, INSET), (MS_L - INSET, INSET),
                          (INSET, MS_W - INSET), (MS_L - INSET, MS_W - INSET)]),
    "MAG": dict(x0=MAG_X0, z0=MAG_Z0, L=MAG_L, W=MAG_W, comp=COMP_H,
                holes=[(2.4, MAG_W / 2), (MAG_L - 2.4, MAG_W / 2)]),
    "BOOST": dict(x0=BOOST_X0, z0=BOOST_Z0, L=BOOST_L, W=BOOST_W, comp=COMP_H,
                  holes=[(INSET, INSET), (BOOST_L - INSET, INSET),
                         (INSET, BOOST_W - INSET), (BOOST_L - INSET, BOOST_W - INSET)]),
    "PROMICRO": dict(x0=PM_X0, z0=PM_Z0, L=PM_L, W=PM_W, comp=COMP_H, tray=True,
                     holes=[(3.0, 3.0), (PM_L - 3.0, 3.0),
                            (3.0, PM_W - 3.0), (PM_L - 3.0, PM_W - 3.0)]),
    "BABY": dict(x0=BABY_X0, z0=BABY_Z0, L=BABY_L, W=BABY_W, comp=COMP_H,
                 holes=[(INSET, INSET), (BABY_L - INSET, INSET),
                        (INSET, BABY_W - INSET), (BABY_L - INSET, BABY_W - INSET)]),
    "BMP581": dict(x0=BMP_X0, z0=BMP_Z0, L=BMP581_L, W=BMP581_W, comp=COMP_H, bay=True,
                   holes=[(INSET, INSET), (BMP581_L - INSET, INSET),
                          (INSET, BMP581_W - INSET), (BMP581_L - INSET, BMP581_W - INSET)]),
}


def board_standoffs(b: dict) -> list[tuple[float, float]]:
    return list(b["holes"])


def board_keepout(b: dict) -> tuple[float, float, float, float, float, float]:
    """3-D keepout (I6): PCB plus everything hanging off it, in world coords."""
    return (b["x0"], b["x0"] + b["L"],
            Y_PCB - PCB_T - b["comp"], Y_PCB + 1.0,
            b["z0"], b["z0"] + b["W"])


# I8: every board is screwed into inserts — an empty hole list is how v2's
# wall-mount rework silently dropped the Pro Micro's fasteners.
for _n, _b in BOARDS.items():
    assert len(_b["holes"]) >= 2, f"I8 {_n} has fewer than 2 insert positions"
    _x0, _x1, _y0, _y1, _z0, _z1 = board_keepout(_b)
    assert _z0 >= INNER_Z0 + 0.5 and _z1 <= INNER_Z1 - 0.5, f"I6 {_n} outside interior Z"
    # I6/I1: the wall land behind this board must stay inside the skin, with
    # room for the insert and the screw relief that continues past it.
    for _xx in (_x0, _x1):
        for _zz in (_z0, _z1):
            _sk = skin_y_plus(_xx, _zz, WALL)
            assert _sk >= Y_LAND + SCREW_RELIEF_FLOOR + 1.5, (
                f"I1 {_n}: inner skin y={_sk:.1f} at x={_xx:.1f} z={_zz:.1f} "
                f"cannot host the land at y={Y_LAND}"
            )
# I6: neighbours must not collide.
_names = list(BOARDS)
for _i in range(len(_names)):
    for _j in range(_i + 1, len(_names)):
        _a, _bk = board_keepout(BOARDS[_names[_i]]), board_keepout(BOARDS[_names[_j]])
        _ox = min(_a[1], _bk[1]) - max(_a[0], _bk[0])
        _oz = min(_a[5], _bk[5]) - max(_a[4], _bk[4])
        assert _ox <= 0 or _oz <= 0, f"I6 {_names[_i]}/{_names[_j]} keepouts overlap"

print(f"pod v3  OUTER L x W x H = {OUTER_L:.1f} x {OUTER_W:.1f} x {OUTER_H:.1f}")
print(f"  aero: fineness {FINENESS:.2f} (D_eq {D_EQ:.1f})  nose {NOSE_LEN / D_EQ:.2f}D"
      f"  boattail {TAIL_LEN / D_EQ:.2f}D  angles nose {NOSE_ANGLE:.0f}deg / tail {BOATTAIL_ANGLE:.1f}deg"
      f"  base/frontal {BASE_A / FRONTAL_A:.2f}")
print(f"  loft: {len(_STATIONS)} stations, ruled; max chord error "
      f"{_ERR:.4f} mm at x={_ERR_X:.0f}")
print(f"  P0: L+H = {OUTER_L + OUTER_H:.1f} <= {BED_LIMIT * math.sqrt(2.0):.1f}"
      f"  (45deg AABB {(OUTER_L + OUTER_H) / math.sqrt(2.0):.1f} <= {BED_LIMIT:.0f})")


# =============================================================================
# HELPERS
# =============================================================================
def _union_if_solid(body: cq.Workplane, part: cq.Workplane) -> cq.Workplane:
    """Union only when a clip left a real solid (skip empty scraps)."""
    try:
        solids = part.val().Solids()
    except Exception:
        return body
    if not solids or sum(s.Volume() for s in solids) < 1e-3:
        return body
    return body.union(part)


def _cyl_y(x: float, z: float, y0: float, length: float, r: float) -> cq.Workplane:
    """Cylinder along Y from y0, extending +length in +Y (negative for -Y).

    The explicit negation matters: `rotate=(90, 0, 0)` leaves the workplane's
    extrude axis pointing at -Y, so a bare `.extrude(length)` runs BACKWARDS
    from what every call site here means.  With that sign wrong the standoffs
    grow into the cavity instead of into the wall land, the flange inserts land
    in the left half, and the static ports drill outward.  v2 carried the same
    trap ("XZ workplane sign flips previously cut screws into empty space");
    _assert_axis_conventions() below is the regression test.
    """
    return (
        cq.Workplane("XY")
        .transformed(offset=(x, y0, z), rotate=(90, 0, 0))
        .circle(r)
        .extrude(-length)
    )


def _cyl_x(x0: float, y: float, z: float, length: float, r: float) -> cq.Workplane:
    """Cylinder along X from x0, extending +length in +X."""
    return (
        cq.Workplane("XY")
        .transformed(offset=(x0, y, z), rotate=(0, 90, 0))
        .circle(r)
        .extrude(length)
    )


def _assert_axis_conventions() -> None:
    bb = _cyl_y(0.0, 0.0, 5.0, 10.0, 1.0).val().BoundingBox()
    assert abs(bb.ymin - 5.0) < 1e-6 and abs(bb.ymax - 15.0) < 1e-6, (
        f"_cyl_y(+length) must run +Y; got y {bb.ymin:.2f}..{bb.ymax:.2f}"
    )
    bb = _cyl_x(5.0, 0.0, 0.0, 10.0, 1.0).val().BoundingBox()
    assert abs(bb.xmin - 5.0) < 1e-6 and abs(bb.xmax - 15.0) < 1e-6, (
        f"_cyl_x(+length) must run +X; got x {bb.xmin:.2f}..{bb.xmax:.2f}"
    )


_assert_axis_conventions()


def _x_slab(x0: float, x1: float) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .transformed(offset=(x0, -OUTER_W, -OUTER_H))
        .box(x1 - x0, 4 * OUTER_W, 3 * OUTER_H, centered=(False, False, False))
    )


def _y_band(ylo: float, yhi: float) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .transformed(offset=(-2.0, ylo, -OUTER_H))
        .box(OUTER_L + 4.0, yhi - ylo, 3 * OUTER_H, centered=(False, False, False))
    )


def _keep_half(side: int) -> cq.Workplane:
    """Bisect at the seam.  Right = +Y (electronics); left = -Y (cover)."""
    ylo, yhi = (0.0, RIGHT_EXTENT + 2.0) if side > 0 else (-LEFT_EXTENT - 2.0, 0.0)
    return _y_band(ylo, yhi)


def flange_screw_stations() -> list[tuple[float, float]]:
    """(x, z) of every clamshell screw, on the top and bottom rails.

    The bottom row follows the boattail upsweep instead of sitting at a fixed
    z, so the aft screws stay on the rail rather than drifting into the skin.
    """
    x0, x1 = FLANGE_X0 + 8.0, FLANGE_X1 - 8.0
    n = max(2, int(round((x1 - x0) / FLANGE_SCREW_PITCH)) + 1)
    out = []
    for i in range(n):
        x = x0 + (x1 - x0) * i / (n - 1)
        _, _, sz0, sz1, _, _, _, _ = section_params(x)
        out.append((x, sz1 - SHELL_SCREW_INSET))
        out.append((x, sz0 + SHELL_SCREW_INSET))
    # Two more through the SUN clamp itself.  The flange proper starts at
    # x=FLANGE_X0, which left nothing holding the halves together over the
    # thread band, and print-2 showed the nose spreading at the press fit.
    # These sit just above the bore, in the widened clamp land.
    out += CLAMP_SCREWS
    return out


FLANGE_SCREWS = flange_screw_stations()


def _rail_shell(inner_inset: float, outer_inset: float) -> cq.Workplane:
    """The band of the section between two insets — a perimeter rail that
    follows the skin exactly, because it is the same section family again."""
    return full_body_solid(inner_inset).cut(full_body_solid(outer_inset))


def _seam_zone(thick: float) -> cq.Workplane:
    """Where the shell actually crosses the seam plane: the top and bottom
    strips, following the section at every station.

    The flange rail must be clipped to this.  A band offset inward from the
    skin exists on the FLANKS too, and on the 10 mm-deep left cover that flank
    band (y -7.5..-3.5) falls inside the +/-FLANGE_W clip and comes through as
    a 2.5 mm plate filling the whole cavity — 30.9 cm^3 of it, blocking the
    battery and SUN install (I4).  The right half never showed it because its
    flank sits at y=+39.5, far outside the clip, which is exactly why the bug
    survived: the halves are asymmetric and only one of them was wrong.
    """
    def zone(which: str) -> cq.Workplane:
        wires = []
        for x in _STATIONS:
            _y0, _y1, z0, z1 = section_params(x)[:4]
            za, zb = (z1 - thick, z1 + 2.0) if which == "top" else (z0 - 2.0, z0 + thick)
            wires.append(cq.Wire.makePolygon([
                cq.Vector(x, -OUTER_W, za), cq.Vector(x, 2 * OUTER_W, za),
                cq.Vector(x, 2 * OUTER_W, zb), cq.Vector(x, -OUTER_W, zb),
            ], close=True))
        return cq.Workplane("XY").newObject([cq.Solid.makeLoft(wires, ruled=True)])

    return zone("top").union(zone("bottom"))


def hollow_half(side: int) -> cq.Workplane:
    """Shell + mating flange rail (+ gasket groove on the right)."""
    shell = full_body_solid(0.0).cut(full_body_solid(WALL))
    body = shell.intersect(_keep_half(side))

    # I4: perimeter rail + local screw bosses, NOT a bulkhead across the bay —
    # the battery and boards go in through the open mating face.
    rail = (
        _rail_shell(WALL, WALL + FLANGE_RAIL)
        .intersect(_x_slab(FLANGE_X0, FLANGE_X1))
        .intersect(_y_band(0.0, FLANGE_W) if side > 0 else _y_band(-FLANGE_W, 0.0))
        .intersect(_seam_zone(WALL + FLANGE_RAIL + 1.0))
    )
    body = _union_if_solid(body, rail)

    for x, z in FLANGE_SCREWS:
        if side > 0:
            # Right: the insert sits near the seam, deep inside a 42 mm half.
            boss = _cyl_y(x, z, 0.0, FLANGE_W, BOSS_D / 2)
        else:
            # Left: the boss must run the FULL depth, outer skin to seam.  Stop
            # it at FLANGE_W and the screw head bears on bare 2.5 mm skin, which
            # the Ø5 x 2.2 counterbore then reduces to a 0.3 mm floor that would
            # tear out under torque.  W1 caught exactly that.
            y_out = section_params(x)[0]
            boss = _cyl_y(x, z, y_out, -y_out, BOSS_D / 2)
        body = _union_if_solid(body, boss.intersect(full_body_solid(0.0)))

    if side > 0:
        # S2: O-cord groove in the right rail (RTV on the flange is the backup).
        mid = WALL + GASKET_W / 2.0  # hugs the skin so the battery notch misses it
        groove = (
            _rail_shell(mid - GASKET_W / 2.0, mid + GASKET_W / 2.0)
            .intersect(_x_slab(FLANGE_X0, FLANGE_X1))
            .intersect(_y_band(0.0, GASKET_D))
        )
        try:
            if groove.val().Volume() > 1e-3:
                body = body.cut(groove)
        except Exception:
            pass
    return body


def add_flange_fasteners(body: cq.Workplane, side: int) -> cq.Workplane:
    """P6: inserts in the RIGHT flange, clearance + counterbore through the left.
    Screw relief continues past the insert (long M2.5s can pass the brass)."""
    for x, z in FLANGE_SCREWS:
        if side > 0:
            body = body.cut(_cyl_y(x, z, 0.0, INS_DEPTH, INS_HOLE_D / 2))
            body = body.cut(_cyl_y(x, z, 0.0, FLANGE_W - 1.0, SCREW_RELIEF_D / 2))
        else:
            # Run the clearance hole to the OUTER skin, not to FLANGE_W.  The
            # left cover is up to 10 mm deep but the hole was cut only 7 mm, so
            # forward of the boattail it stopped 0.8 mm short of the
            # counterbore floor and the screw had no way through.  Only the two
            # aftmost stations worked, where the taper has thinned the cover.
            outer_y = section_params(x)[0]
            body = body.cut(_cyl_y(x, z, 1.0, (outer_y - 2.0) - 1.0, LID_SCREW_D / 2))
            body = body.cut(_cyl_y(x, z, outer_y - 0.5, LID_CB_DEPTH + 0.5, LID_CB_D / 2))
    return body


# =============================================================================
# ESA SUN-B PITOT CRADLE
# =============================================================================
def _cradle_plate(x0: float, xlen: float, side: int) -> cq.Workplane:
    """Bulkhead blank near the pitot bore, clipped to the outer envelope (I1).
    Limited to CRADLE_LAND_Y so the hose run to the MS4525 stays open."""
    ylo, yhi = (0.0, CRADLE_LAND_Y) if side > 0 else (-CRADLE_LAND_Y, 0.0)
    blank = (
        cq.Workplane("XY")
        .transformed(offset=(x0, ylo, 0.0))
        .box(xlen, yhi - ylo, OUTER_H, centered=(False, False, False))
    )
    return blank.intersect(full_body_solid(0.0)).intersect(_keep_half(side))


def _sun_bore(x0: float, x1: float, r: float) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .transformed(offset=(x0, 0.0, PITOT_AXIS_Z), rotate=(0, 90, 0))
        .circle(r)
        .extrude(x1 - x0)
    )


def add_pitot_cradle(body: cq.Workplane, side: int) -> cq.Workplane:
    """Split L/R cradle on the SUN's own features — no saddle, no tubes-in-tube.

    v3 mounts on the Ø11.76 threaded band (the feature ESA put there for it)
    instead of the Ø8.93 shoulder.  The forward stop is the Ø10.65 -> Ø11.76
    step landing on the nose bulkhead's AFT face, which is a bigger step than
    the shoulder it replaces, and it moves the SUN aft face from x=103 to
    x=79 — the 24 mm that pays for the boattail.
    """
    # Nose bulkhead: bore passes the smooth barrel; the thread step stops on it.
    body = _union_if_solid(body, _cradle_plate(NOSE_BH_X0, SHOULDER_BH_T, side))
    # Clamp land on the threaded band (thick meat around a snug bore).
    clamp = _cradle_plate(SUN_THREAD_X0, SUN_THREAD_X1 - SUN_THREAD_X0, side).intersect(
        _sun_bore(SUN_THREAD_X0 - 1.0, SUN_THREAD_X1 + 1.0, CLAMP_R_OUTER)
    )
    body = _union_if_solid(body, clamp)
    # Two barrel bulkheads + the aft bulkhead carrying the locating boss.
    # ONE barrel bulkhead, not two.  The second sat at x 60.3..63.8, directly
    # across the heat-set corridor for MS4525's and MAG's aft pilots (I7), and
    # a cradle plate spans the full Y depth of the cradle land so no approach
    # angle gets past it.  The SUN is already held by the nose bulkhead, a
    # 25 mm clamp on the thread, this bulkhead and the aft bulkhead + boss;
    # the free span it leaves is 46 mm of Ø11.71 brass.
    mid_bh_x = 0.5 * (SUN_BARREL_X0 + SUN_BARB_X[0]) - 1.75
    body = _union_if_solid(body, _cradle_plate(mid_bh_x, 3.5, side))
    body = _union_if_solid(body, _cradle_plate(AFT_BH_X0, SHOULDER_BH_T, side))

    # Bores, forward to aft.  Mouth lip is NOSE_LIP_WALL of PETG (P7).
    body = body.cut(_sun_bore(-1.0, NOSE_BH_X1, CRADLE_R_SMOOTH))
    body = body.cut(_sun_bore(NOSE_BH_X1, SUN_THREAD_X1, CRADLE_R_CLAMP))
    body = body.cut(_sun_bore(SUN_THREAD_X1, AFT_BH_X0, CRADLE_R_BARREL))

    # Barb bay: the three barbs point up and must not be caged.
    barb_r = SUN_BARB_STEM_OD / 2 + 2.0
    bay = (
        cq.Workplane("XY")
        .transformed(offset=(SUN_BARB_X[0] - barb_r - 2.0,
                             -barb_r if side < 0 else 0.0,
                             PITOT_AXIS_Z))
        .box((SUN_BARB_X[2] + barb_r + 2.0) - (SUN_BARB_X[0] - barb_r - 2.0),
             barb_r, min(SUN_BARB_TIP_Z + 8.0, INNER_Z1) - PITOT_AXIS_Z,
             centered=(False, False, False))
    )
    # Clip the bay to the CAVITY.  Unclipped it ate the nose's upper skin: at
    # x=33 only 0.32 mm of PETG was left above the cut and it printed as a slit
    # across the seam, open to the airflow — an undeclared S1 opening.  The
    # cradle plates it has to clear are all inside the cavity anyway.
    body = body.cut(bay.intersect(full_body_solid(WALL)))

    # Aft locating boss into the Ø6.03 x 7.06 blind cup.  Print-1: a boss ~2 mm
    # too long held the SUN off its stops, so >=2.5 mm of the cup stays unused.
    boss = _sun_bore(AFT_BH_X0 - SUN_RECESS_BOSS_LEN, AFT_BH_X0, SUN_RECESS_BOSS_D / 2)
    body = _union_if_solid(body, boss.intersect(_keep_half(side)))
    return body


# =============================================================================
# BATTERY / ELECTRONICS WALL / STATIC BAY
# =============================================================================
def _box(x0: float, y0: float, z0: float, dx: float, dy: float, dz: float) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .transformed(offset=(x0, y0, z0))
        .box(dx, dy, dz, centered=(False, False, False))
    )


def add_battery_pocket(body: cq.Workplane) -> cq.Workplane:
    """L1/S1: notch interior material only — the pocket never reaches the skin,
    and it must not erase the sealing land or a flange screw boss."""
    tool = _box(BATT_X0, BATT_Y0, BATT_Z0,
                BATT_POCKET_X, BATT_POCKET_Y, BATT_POCKET_Z)
    tool = tool.intersect(full_body_solid(WALL + BATT_SEAL_KEEP))
    for x, z in FLANGE_SCREWS:
        if BATT_X0 - BOSS_D <= x <= BATT_X0 + BATT_POCKET_X + BOSS_D:
            tool = tool.cut(_cyl_y(x, z, -FLANGE_W - 1.0, 2 * FLANGE_W + 2.0, BOSS_D / 2))
    try:
        if tool.val().Volume() < 1.0:
            return body
    except Exception:
        return body
    return body.cut(tool)


def insert_stack_cuts(body: cq.Workplane, x: float, z: float) -> cq.Workplane:
    """P6: pilot for the heat-set insert, then screw relief DEEPER than the
    insert so a long M2.5 can pass the brass instead of jacking the board off."""
    body = body.cut(_cyl_y(x, z, Y_PCB, INS_DEPTH, INS_HOLE_D / 2))
    skin = skin_y_plus(x, z, WALL)
    relief = min(INS_DEPTH + SCREW_RELIEF_EXTRA, skin - SCREW_RELIEF_FLOOR - Y_PCB)
    if relief > INS_DEPTH:
        body = body.cut(_cyl_y(x, z, Y_PCB, relief, SCREW_RELIEF_D / 2))
    return body


def add_electronics_wall(body: cq.Workplane) -> cq.Workplane:
    """I5/I8: boards wall-mount on the +Y land on RAISED standoffs, inserts
    along Y so the iron enters from the open mating face.  No floor posts —
    print-1 showed an iron cannot reach the bottom of a well."""
    xs = [b["x0"] for b in BOARDS.values()] + [b["x0"] + b["L"] for b in BOARDS.values()]
    zs = [b["z0"] for b in BOARDS.values()] + [b["z0"] + b["W"] for b in BOARDS.values()]
    land = _box(min(xs) - 4.0, Y_LAND, max(min(zs) - 4.0, INNER_Z0),
                (max(xs) + 4.0) - (min(xs) - 4.0), WALL_LAND_T + 6.0,
                min(max(zs) + 4.0, INNER_Z1) - max(min(zs) - 4.0, INNER_Z0))
    body = _union_if_solid(body, land.intersect(full_body_solid(0.0)))

    for b in BOARDS.values():
        for hx, hz in board_standoffs(b):
            x, z = b["x0"] + hx, b["z0"] + hz
            post = _cyl_y(x, z, Y_PCB, Y_LAND + 3.0 - Y_PCB, BOARD_POST_D / 2)
            body = _union_if_solid(body, post.intersect(full_body_solid(0.0)))
    for b in BOARDS.values():
        for hx, hz in board_standoffs(b):
            body = insert_stack_cuts(body, b["x0"] + hx, b["z0"] + hz)
    return body


def static_hole_centers() -> list[tuple[float, float]]:
    cx, cz = STATIC_PORT_X, STATIC_PORT_Z
    out = []
    for r in range(STATIC_HOLE_ROWS):
        for c in range(STATIC_HOLE_COLS):
            out.append((
                cx + (c - (STATIC_HOLE_COLS - 1) / 2) * STATIC_HOLE_PITCH_X,
                cz + (r - (STATIC_HOLE_ROWS - 1) / 2) * STATIC_HOLE_PITCH_Z,
            ))
    return out


def cup_screws() -> list[tuple[float, float]]:
    """Cup screw stations — on the +/-Z tabs, clear of the BMP footprint.

    Z has 9.5 mm free below the plenum and 13 above, so tabs there cost
    nothing.  On +/-X they would push the Babysitter ~9 mm aft.
    """
    cx = 0.5 * (BAY_X0 + BAY_X1)
    return [(cx - 11.0, CUP_Z0 - CUP_TAB), (cx + 11.0, CUP_Z0 - CUP_TAB),
            (cx - 11.0, CUP_Z1 + CUP_TAB), (cx + 11.0, CUP_Z1 + CUP_TAB)]


def add_static_bay(body: cq.Workplane) -> cq.Workplane:
    """S3/L3: the right half provides a flat sealing land, four inserts and the
    static port array.  The plenum walls belong to static_bay.stl, so the BMP
    stands on a completely open wall while its inserts go in."""
    for cx, cz in cup_screws():
        body = body.cut(_cyl_y(cx, cz, Y_LAND, INS_DEPTH, INS_HOLE_D / 2))
        skin = skin_y_plus(cx, cz, WALL)
        relief = min(INS_DEPTH + SCREW_RELIEF_EXTRA, skin - SCREW_RELIEF_FLOOR - Y_LAND)
        if relief > INS_DEPTH:
            body = body.cut(_cyl_y(cx, cz, Y_LAND, relief, SCREW_RELIEF_D / 2))
    for hx, hz in static_hole_centers():
        body = body.cut(_cyl_y(hx, hz, RIGHT_EXTENT + 1.0,
                               -(RIGHT_EXTENT + 1.0 - (Y_LAND - 1.0)),
                               STATIC_HOLE_D / 2))
    return body


# =============================================================================
# TAIL RIM + SERVICE PANEL + LABYRINTH DRAIN
# =============================================================================
TAIL_RIM_T = 8.0    # X depth of the rim the panel screws into
TAIL_RIM_W = 5.0    # how far the rim reaches inward from the inner skin
# 3.0 sits inside the R1966A snap-in band (2.0-3.0 mm), so the rocker clips
# straight into the plate with no rebate to print and no thin section.
PANEL_T = 3.0

# Service cluster, stacked in Z on the aft face (y = across, z = up).
SW_CUT_Y, SW_CUT_Z = 19.6, 13.0     # COM-08837 / E-Switch R1966A snap-in
USB_WIN_Y, USB_WIN_Z = 10.5, 7.5    # CAB-15464 window
USB_EAR_PITCH = 17.0
M3_CLR_D = 3.3
LED_HOLE_D = 8.2                     # 5 mm chrome ABS holder, 8 mm panel hole
LED_DY = 6.0
PANEL_GAP_Z = 3.0
M3_NUT_AF = 5.5          # across flats, behind the plate at each USB ear
PANEL_CY = 0.5 * (BASE_Y0 + BASE_Y1)

# The opening the cluster has to pass through, straight out of the section
# family: base section inset by the wall plus the insert rim.
_OP = section_params(OUTER_L)
OPEN_Y0 = _OP[0] + WALL + TAIL_RIM_W
OPEN_Y1 = _OP[1] - WALL - TAIL_RIM_W
OPEN_Z0 = _OP[2] + WALL + TAIL_RIM_W
OPEN_Z1 = _OP[3] - WALL - TAIL_RIM_W

# Stack the cluster in Z and CENTRE it in the opening rather than hard-coding
# heights — hard-coded ones are what fouled the rim at the first base size.
_STACK = SW_CUT_Z + PANEL_GAP_Z + USB_WIN_Z + PANEL_GAP_Z + LED_HOLE_D
_TOP = 0.5 * (OPEN_Z0 + OPEN_Z1) + _STACK / 2
SW_Z = _TOP - SW_CUT_Z / 2
USB_Z = _TOP - SW_CUT_Z - PANEL_GAP_Z - USB_WIN_Z / 2
LED_Z = _TOP - SW_CUT_Z - PANEL_GAP_Z - USB_WIN_Z - PANEL_GAP_Z - LED_HOLE_D / 2
TAIL_SCREWS = [(7.0, 12.0), (24.0, 12.0), (7.0, 56.0), (24.0, 56.0)]

# L6: every part of the cluster must clear the opening, including the nuts
# behind the USB ears.  This is the check that the first base size failed.
_CLUSTER = [
    ("rocker", SW_CUT_Y, SW_CUT_Z, SW_Z),
    ("USB window", USB_WIN_Y, USB_WIN_Z, USB_Z),
    ("USB ear nuts", USB_EAR_PITCH + M3_NUT_AF, M3_NUT_AF, USB_Z),
    ("LED holders", 2 * LED_DY + LED_HOLE_D, LED_HOLE_D, LED_Z),
]
for _n, _dy, _dz, _cz in _CLUSTER:
    assert PANEL_CY - _dy / 2 >= OPEN_Y0 + 0.5 and PANEL_CY + _dy / 2 <= OPEN_Y1 - 0.5, (
        f"L6 {_n} ({_dy:.1f} wide) fouls the base opening "
        f"y {OPEN_Y0:.1f}..{OPEN_Y1:.1f} — enlarge BASE_W or thin TAIL_RIM_W"
    )
    assert _cz - _dz / 2 >= OPEN_Z0 + 0.5 and _cz + _dz / 2 <= OPEN_Z1 - 0.5, (
        f"L6 {_n} at z={_cz:.1f} (+/-{_dz / 2:.1f}) fouls the base opening "
        f"z {OPEN_Z0:.1f}..{OPEN_Z1:.1f} — enlarge BASE_H"
    )
for _cy, _cz in TAIL_SCREWS:
    assert _cy - BOSS_D / 2 > 0.5, (
        f"I3 aft-panel boss at y={_cy} straddles the seam (it runs along X)"
    )

# Drain (S1 amendment): baffled so it sheds condensate and equalises pressure
# without becoming a ram-air inlet.  Sits at the aft end of the flat bottom,
# which is the low point of the cavity, clear of the battery and the rails.
DRAIN_X0, DRAIN_X1 = 158.0, 172.0
DRAIN_Y0, DRAIN_Y1 = 5.5, 13.5
DRAIN_ROOF = INNER_Z0 + 4.0
DRAIN_D = 2.0


def _panel_cut_shapes(depth_out: float, depth_in: float) -> list[cq.Workplane]:
    """The L6 cluster, cut along X.  Same list drives the plate and the QC PNG."""
    def bx(cy, cz, dy, dz):
        return _box(OUTER_L - depth_in, cy - dy / 2, cz - dz / 2,
                    depth_in + depth_out, dy, dz)

    def cyl(cy, cz, d):
        return (
            cq.Workplane("XY")
            .transformed(offset=(OUTER_L - depth_in, cy, cz), rotate=(0, 90, 0))
            .circle(d / 2)
            .extrude(depth_in + depth_out)
        )

    out = [bx(PANEL_CY, SW_Z, SW_CUT_Y, SW_CUT_Z),
           bx(PANEL_CY, USB_Z, USB_WIN_Y, USB_WIN_Z)]
    for dy in (-USB_EAR_PITCH / 2, USB_EAR_PITCH / 2):
        out.append(cyl(PANEL_CY + dy, USB_Z, M3_CLR_D))
    for dy in (-LED_DY, LED_DY):
        out.append(cyl(PANEL_CY + dy, LED_Z, LED_HOLE_D))
    return out


def add_tail_rim(body: cq.Workplane, side: int) -> cq.Workplane:
    """Rim the service plate bolts to.  Same _rail_shell trick as the flange,
    so it follows the base section exactly instead of being a pasted-on box."""
    rim = (
        _rail_shell(WALL, WALL + TAIL_RIM_W)
        .intersect(_x_slab(OUTER_L - TAIL_RIM_T, OUTER_L))
        .intersect(_keep_half(side))
    )
    body = _union_if_solid(body, rim)
    if side > 0:
        for cy, cz in TAIL_SCREWS:
            boss = (
                cq.Workplane("XY")
                .transformed(offset=(OUTER_L - TAIL_RIM_T, cy, cz), rotate=(0, 90, 0))
                .circle(BOSS_D / 2)
                .extrude(TAIL_RIM_T)
            )
            body = _union_if_solid(body, boss.intersect(full_body_solid(0.0)))
            pilot = (
                cq.Workplane("XY")
                .transformed(offset=(OUTER_L - INS_DEPTH, cy, cz), rotate=(0, 90, 0))
                .circle(INS_HOLE_D / 2)
                .extrude(INS_DEPTH + 1.0)
            )
            relief = (
                cq.Workplane("XY")
                .transformed(offset=(OUTER_L - INS_DEPTH - SCREW_RELIEF_EXTRA, cy, cz),
                             rotate=(0, 90, 0))
                .circle(SCREW_RELIEF_D / 2)
                .extrude(INS_DEPTH + SCREW_RELIEF_EXTRA + 1.0)
            )
            body = body.cut(relief).cut(pilot)
    return body


def add_drain(body: cq.Workplane) -> cq.Workplane:
    """Labyrinth: cavity -> slot at the aft end wall -> channel -> down and out.
    No straight path from freestream to interior."""
    boss = _box(DRAIN_X0, DRAIN_Y0, INNER_Z0,
                DRAIN_X1 - DRAIN_X0, DRAIN_Y1 - DRAIN_Y0, DRAIN_ROOF + 1.5 - INNER_Z0)
    body = _union_if_solid(body, boss.intersect(full_body_solid(0.0)))
    chan = _box(DRAIN_X0 + 2.0, DRAIN_Y0 + 2.0, INNER_Z0,
                (DRAIN_X1 - 2.0) - (DRAIN_X0 + 2.0),
                (DRAIN_Y1 - 2.0) - (DRAIN_Y0 + 2.0), DRAIN_ROOF - INNER_Z0)
    body = body.cut(chan)
    cy = 0.5 * (DRAIN_Y0 + DRAIN_Y1)
    # inner slot at floor level, in the AFT end wall (water reaches it by gravity)
    body = body.cut(_box(DRAIN_X1 - 2.5, cy - 1.0, INNER_Z0, 3.0, 2.0, 1.2))
    # outer hole through the skin, forward end of the channel
    body = body.cut(
        cq.Workplane("XY")
        .transformed(offset=(DRAIN_X0 + 4.0, cy, -1.0))
        .circle(DRAIN_D / 2)
        .extrude(INNER_Z0 + 2.0)
    )
    return body


# =============================================================================
# TOP-LEVEL PARTS
# =============================================================================
VERBOSE = False


def _step(body: cq.Workplane, fn, *args, label: str = "") -> cq.Workplane:
    """Run a build step, optionally timing it.  Booleans against the ~390-face
    envelope dominate the runtime, so knowing which step costs what matters."""
    if not VERBOSE:
        return fn(body, *args)
    import time
    t = time.time()
    out = fn(body, *args)
    print(f"      {label or fn.__name__}: {time.time() - t:.1f}s")
    return out


_PART_CACHE: dict[str, cq.Workplane] = {}


def _cached(name: str, fn):
    """Both main() and validate_pod ask for the halves; building each twice
    doubles a ~4 minute run for nothing."""
    if name not in _PART_CACHE:
        _PART_CACHE[name] = fn()
    return _PART_CACHE[name]


def build_right() -> cq.Workplane:
    return _cached("right", _build_right)


def build_left() -> cq.Workplane:
    return _cached("left", _build_left)


def _build_right() -> cq.Workplane:
    body = hollow_half(1)
    for fn, args in ((add_flange_fasteners, (1,)), (add_pitot_cradle, (1,)),
                     (add_battery_pocket, ()), (add_electronics_wall, ()),
                     (add_static_bay, ()), (add_tail_rim, (1,)), (add_drain, ())):
        body = _step(body, fn, *args)
    return body


def _build_left() -> cq.Workplane:
    body = hollow_half(-1)
    for fn, args in ((add_flange_fasteners, (-1,)), (add_pitot_cradle, (-1,)),
                     (add_battery_pocket, ()), (add_tail_rim, (-1,))):
        body = _step(body, fn, *args)
    return body


def build_tail_panel() -> cq.Workplane:
    """L6 cluster on the aft face: rocker, USB, two LED holders.  Flat plate,
    populated on the bench — the nuts and jam nuts are all reachable."""
    face = cq.Face.makeFromWires(section_wire(OUTER_L, 0.0))
    body = cq.Workplane("XY").newObject(
        [cq.Solid.extrudeLinear(face, cq.Vector(PANEL_T, 0.0, 0.0))]
    )
    for cut in _panel_cut_shapes(PANEL_T + 2.0, 2.0):
        body = body.cut(cut)
    for cy, cz in TAIL_SCREWS:
        body = body.cut(
            cq.Workplane("XY")
            .transformed(offset=(OUTER_L - 1.0, cy, cz), rotate=(0, 90, 0))
            .circle(LID_SCREW_D / 2).extrude(PANEL_T + 2.0)
        )
        body = body.cut(
            cq.Workplane("XY")
            .transformed(offset=(OUTER_L + PANEL_T - LID_CB_DEPTH, cy, cz), rotate=(0, 90, 0))
            .circle(LID_CB_D / 2).extrude(LID_CB_DEPTH + 1.0)
        )
    return body


def build_static_bay_cup() -> cq.Workplane:
    """S3: the isolated BMP plenum, as a cup that seals to the wall land.

    Replaces the old integral walls + tool window + cover plate.  The BMP is
    mounted and its inserts heat-set on a completely open wall first; the cup
    then goes over it on a foam/RTV gasket with four M2.5 into the land.
    Print it CLOSED FACE DOWN — the flange tabs are then a short overhang at
    the top rather than a 29 mm bridge over the roof.
    """
    body = _box(CUP_X0, CUP_Y0, CUP_Z0,
                CUP_X1 - CUP_X0, Y_LAND - CUP_Y0, CUP_Z1 - CUP_Z0)
    body = body.cut(_box(BAY_X0, CUP_Y0 + CUP_WALL, BAY_Z0,
                         BAY_X1 - BAY_X0, (Y_LAND - CUP_Y0 - CUP_WALL) + 1.0,
                         BAY_Z1 - BAY_Z0))
    for z0, z1 in ((CUP_Z0 - CUP_TAB - 3.5, CUP_Z0), (CUP_Z1, CUP_Z1 + CUP_TAB + 3.5)):
        body = body.union(_box(CUP_X0, Y_LAND - CUP_FLANGE, z0,
                               CUP_X1 - CUP_X0, CUP_FLANGE, z1 - z0))
    for cx, cz in cup_screws():
        body = body.cut(_cyl_y(cx, cz, Y_LAND - CUP_FLANGE - 1.0,
                               CUP_FLANGE + 2.0, LID_SCREW_D / 2))
        body = body.cut(_cyl_y(cx, cz, Y_LAND - CUP_FLANGE - 0.1,
                               LID_CB_DEPTH + 0.1, LID_CB_D / 2))
    # Qwiic gland so the I2C tail leaves the sealed plenum.
    body = body.cut(_box(0.5 * (BAY_X0 + BAY_X1) - 3.0, CUP_Y0 - 1.0,
                         BAY_Z1 - 4.0, 6.0, CUP_WALL + 2.0, 4.0))
    return body


def build_pm_tray() -> cq.Workplane:
    """L7: the SparkFun Pro Micro has no OEM mounting holes, so it is clamped
    in a tray and the TRAY screws to the standoffs.

    The retaining rim runs in -Y, away from the wall: the board drops into the
    tray from the open mating face.  It used to run +Y, straight into the wall
    land it is bolted against, which retained nothing and fouled the land by
    1 mm.
    """
    b = BOARDS["PROMICRO"]
    t, rim = 2.2, 3.2
    base_y = Y_PCB - t
    body = _box(b["x0"], base_y, b["z0"], b["L"], t, b["W"])
    for dx, dz, sx, sz in ((0.0, 0.0, b["L"], 1.6), (0.0, b["W"] - 1.6, b["L"], 1.6),
                           (0.0, 0.0, 1.6, b["W"]), (b["L"] - 1.6, 0.0, 1.6, b["W"])):
        body = body.union(_box(b["x0"] + dx, base_y - rim, b["z0"] + dz, sx, rim, sz))
    # window so the Pro Micro's underside parts and its USB tail clear the tray
    body = body.cut(_box(b["x0"] + 4.0, base_y - 1.0, b["z0"] + 3.0,
                         b["L"] - 8.0, t + 2.0, b["W"] - 6.0))
    for hx, hz in board_standoffs(b):
        body = body.cut(_cyl_y(b["x0"] + hx, b["z0"] + hz, base_y - rim - 1.0,
                               t + rim + 2.0, LID_SCREW_D / 2))
    return body


def build_sun_placeholder() -> cq.Workplane:
    """Not printed — purchased brass, shown in the assembly STEP."""
    steps = [
        (SUN_TIP_X0, SUN_SMOOTH_X0, SUN_TIP_OD),
        (SUN_SMOOTH_X0, SUN_THREAD_X0, SUN_SMOOTH_OD),
        (SUN_THREAD_X0, SUN_THREAD_X1, SUN_THREAD_MAJOR),
        (SUN_THREAD_X1, SUN_AFT_X, SUN_BARREL_OD),
    ]
    body = None
    for x0, x1, d in steps:
        seg = _sun_bore(x0, x1, d / 2)
        body = seg if body is None else body.union(seg)
    for bx in SUN_BARB_X:
        body = body.union(
            cq.Workplane("XY")
            .transformed(offset=(bx, 0.0, PITOT_AXIS_Z))
            .circle(SUN_BARB_STEM_OD / 2)
            .extrude(SUN_BARB_TIP_Z - PITOT_AXIS_Z)
        )
    return body


# =============================================================================
# EXPORT
# =============================================================================
def as_single_solid(body: cq.Workplane, name: str) -> cq.Workplane:
    """P1: AnkerMake rejects multi-body compounds."""
    solids = body.val().Solids()
    if len(solids) <= 1:
        return body
    fused = solids[0]
    for s in solids[1:]:
        fused = fused.fuse(s)
    out = sorted(fused.Solids(), key=lambda s: s.Volume(), reverse=True)
    if len(out) > 1:
        # HARD failure, not a warning.  Silently keeping the largest solid is
        # how a part ships with its standoffs missing — and "a feature that is
        # not actually attached" is the exact defect this redesign exists to
        # eliminate.  Report where they are so they can be fixed, not dropped.
        detail = []
        for sol in out[1:8]:
            bb = sol.BoundingBox()
            detail.append(f"      {sol.Volume():8.1f} mm^3 at x {bb.xmin:.1f}..{bb.xmax:.1f}"
                          f" y {bb.ymin:.1f}..{bb.ymax:.1f} z {bb.zmin:.1f}..{bb.zmax:.1f}")
        more = "" if len(out) <= 8 else f"\n      ... and {len(out) - 8} more"
        raise AssertionError(
            f"P1 {name}: {len(out) - 1} disconnected solid(s), "
            f"{sum(s.Volume() for s in out[1:]):.1f} mm^3 not attached to the body:\n"
            + "\n".join(detail) + more
        )
    return cq.Workplane("XY").newObject([out[0]])


def for_print_half(body: cq.Workplane, side: int) -> cq.Workplane:
    """P0/P4: mating face DOWN, curved outer up, rotated 45 deg for the bed
    diagonal.  The sign is per-side; v2 shipped these swapped once and printed
    the flange on top."""
    ang = 90.0 if side > 0 else -90.0
    out = body.rotate((0, 0, 0), (1, 0, 0), ang)
    out = out.rotate((0, 0, 0), (0, 0, 1), 45.0)
    return _centre_on_bed(out)


def mesh_extents(body: cq.Workplane) -> tuple[float, float, float, float, float, float]:
    """True extents of the TESSELLATED shape, at export tolerance.

    Shape.BoundingBox() is inflated for spline geometry — it grew by 0.74 mm on
    a re-imported STEP — and it is not what gets printed anyway.  The mesh is
    what the slicer sees, so bed placement and the P0 fit are measured on it.
    """
    verts, _tris = body.val().tessellate(0.03, 0.05)
    xs = [v.x for v in verts]
    ys = [v.y for v in verts]
    zs = [v.z for v in verts]
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def _centre_on_bed(body: cq.Workplane) -> cq.Workplane:
    """Sit the part on z=0 and CENTRE it on the bed.

    Not cosmetic: a slicer that honours the STL's own coordinates will place a
    corner-parked part against the bed edge, and then the skirt/brim and any
    support outline land at negative X/Y — "a toolpath outside the print area".
    """
    x0, x1, y0, y1, z0, _z1 = mesh_extents(body)
    return body.translate((
        BED / 2.0 - 0.5 * (x0 + x1),
        BED / 2.0 - 0.5 * (y0 + y1),
        -z0,
    ))


def check_print_bb(body: cq.Workplane, name: str) -> tuple[float, float, float]:
    x0, x1, y0, y1, z0, z1 = mesh_extents(body)
    bb = type("E", (), dict(xmin=x0, xmax=x1, ymin=y0, ymax=y1, zmin=z0, zmax=z1))
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    # 0.01 not 1e-6: OCCT inflates bounding boxes of spline/arc geometry by a
    # tolerance gap, so a part translated by its own reported bbox lands a few
    # microns off zero.  The physical requirement is "on the bed", not "exact".
    assert abs(bb.zmin) < 0.01, f"P4 {name} not sitting on the bed (zmin={bb.zmin})"
    assert dx <= BED_LIMIT and dy <= BED_LIMIT, (
        f"P0 {name} footprint {dx:.1f} x {dy:.1f} exceeds {BED_LIMIT:.0f} mm"
    )
    # The part must sit ON the bed with room for what gets drawn around it.
    assert (bb.xmin - BRIM_ALLOWANCE >= 0.0 and bb.ymin - BRIM_ALLOWANCE >= 0.0
            and bb.xmax + BRIM_ALLOWANCE <= BED
            and bb.ymax + BRIM_ALLOWANCE <= BED), (
        f"P0 {name} at X {bb.xmin:.1f}..{bb.xmax:.1f} Y {bb.ymin:.1f}..{bb.ymax:.1f} "
        f"leaves less than {BRIM_ALLOWANCE:.0f} mm for skirt/brim inside the "
        f"{BED:.0f} mm bed"
    )
    assert dz <= BED_Z, f"P0 {name} height {dz:.1f} exceeds {BED_Z:.0f} mm"
    return dx, dy, dz


def _boundary_edges(path: str) -> int:
    """P2: count edges used an odd number of times in the tessellation."""
    import struct
    from collections import Counter
    with open(path, "rb") as fh:
        head = fh.read(84)
        n = struct.unpack("<I", head[80:84])[0]
        data = fh.read(50 * n)
    seen: Counter = Counter()
    for i in range(n):
        off = 50 * i + 12
        vs = []
        for k in range(3):
            vs.append(tuple(round(v, 5) for v in struct.unpack("<3f", data[off + 12 * k:off + 12 * k + 12])))
        for k in range(3):
            a, b = vs[k], vs[(k + 1) % 3]
            seen[(a, b) if a < b else (b, a)] += 1
    return sum(1 for v in seen.values() if v % 2)


def flat_for_print(body: cq.Workplane, axis: str) -> cq.Workplane:
    """Lay a small flat part on the bed with its thickness in Z, then drop it to
    z=0.  Without this the plates export wherever they sit in model space —
    tail_panel's thickness is along X and the cover's is along Y."""
    if axis == "x":
        body = body.rotate((0, 0, 0), (0, 1, 0), 90.0)
    elif axis == "y":
        body = body.rotate((0, 0, 0), (1, 0, 0), -90.0)
    return _centre_on_bed(body)


def export_part(body: cq.Workplane, name: str, *, stl: bool = True) -> None:
    cq.exporters.export(body, f"{name}.step")
    if not stl:
        return
    cq.exporters.export(body, f"{name}.stl",
                        tolerance=0.03, angularTolerance=0.05)
    nb = _boundary_edges(f"{name}.stl")
    assert nb == 0, f"P2 {name}.stl has {nb} boundary edges (not watertight)"
    print(f"  {name}.stl watertight")


def main() -> None:
    import time
    t0 = time.time()

    def lap(msg: str) -> None:
        print(f"  [{time.time() - t0:6.1f}s] {msg}")

    _assert_exact_envelope()
    lap("envelope extents exact on all four flats")

    right = as_single_solid(build_right(), "pod_right")
    lap(f"pod_right built ({right.val().Volume() / 1000:.1f} cm^3)")
    left = as_single_solid(build_left(), "pod_left")
    lap(f"pod_left built ({left.val().Volume() / 1000:.1f} cm^3)")

    # I2: nothing a half contains may lie outside the outer envelope.
    outer = full_body_solid(0.0)
    for name, half in (("pod_right", right), ("pod_left", left)):
        extra = half.val().cut(outer.val()).Volume()
        assert extra < 25.0, f"I2 {name} protrudes {extra:.1f} mm^3 outside the envelope"
        lap(f"I2 {name} outside envelope {extra:.2f} mm^3")

    panel = build_tail_panel()
    cup = build_static_bay_cup()
    tray = build_pm_tray()

    export_part(for_print_half(right, 1), "pod_right")
    export_part(for_print_half(left, -1), "pod_left")
    for name, part, axis in (("tail_panel", panel, "x"),
                             ("static_bay", cup, "y"),
                             ("pm_tray", tray, "y")):
        flat = flat_for_print(part, axis)
        export_part(flat, name)
        check_print_bb(flat, name)
    lap("STL/STEP exported")

    for name, part, side in (("pod_right", right, 1), ("pod_left", left, -1)):
        dx, dy, dz = check_print_bb(for_print_half(part, side), name)
        print(f"  P0 {name} print AABB {dx:.1f} x {dy:.1f} x {dz:.1f} mm "
              f"(bed {BED_LIMIT:.0f} x {BED_LIMIT:.0f} x {BED_Z:.0f})")

    for fn in (render_layout_png, render_profile_png, render_interior_png,
               render_aft_panel_png):
        lap(f"wrote {fn()}")

    asm = right.union(left).union(build_sun_placeholder())
    cq.exporters.export(asm, "pod_assembly.step")
    lap("pod_assembly.step written")

    from validate_pod import validate_all
    r = validate_all()
    for note in r.notes:
        print(f"  {note}")
    if not r.passed:
        raise SystemExit(f"validate_pod: {len(r.failures)} failure(s)")
    lap("validate_pod green")




def build_routes() -> list[dict]:
    """Named harness/hose polylines, in (x, y, z).

    A layout aid, not geometry — nothing here cuts a solid.  Their job is to
    prove on paper that every run has somewhere to go before the pod is closed,
    and to be drawn on the assembly render.  The long runs deliberately use the
    corridor at y ~5..15, which is clear between the battery slab on the seam
    (y -4..4) and the boards and static cup on the wall (y >= 15.4).
    """
    baby = BOARDS["BABY"]
    ms_z = BOARDS["MS4525"]["z0"] + 12.0
    return [
        dict(name="pitot (total)", colour="tab:red", pts=[
            (SUN_BARB_X[2], 0.0, SUN_BARB_TIP_Z), (SUN_BARB_X[2], 7.0, SUN_BARB_TIP_Z - 2),
            (66.0, 14.0, 32.0), (62.0, 19.0, ms_z)]),
        dict(name="static (airspeed)", colour="tab:orange", pts=[
            (SUN_BARB_X[1], 0.0, SUN_BARB_TIP_Z), (SUN_BARB_X[1], 7.0, SUN_BARB_TIP_Z - 2),
            (57.0, 14.0, 32.0), (57.0, 19.0, ms_z)]),
        dict(name="TE (capped)", colour="0.5", pts=[
            (SUN_BARB_X[0], 0.0, SUN_BARB_TIP_Z), (SUN_BARB_X[0], 0.0, SUN_BARB_TIP_Z + 4)]),
        dict(name="qwiic: BMP -> Pro Micro", colour="tab:blue", pts=[
            (0.5 * (BAY_X0 + BAY_X1), 18.0, BAY_Z1 - 3.0),
            (0.5 * (BAY_X0 + BAY_X1), 13.0, CUP_Z1 + 2.0),
            (CUP_X0 - 4.0, 11.0, CUP_Z1 + 3.0), (104.0, 16.0, 50.0),
            (100.0, 20.0, PM_Z0 + 4.0)]),
        dict(name="qwiic: MAG -> Boost", colour="tab:cyan", pts=[
            (60.0, 20.0, MAG_Z0 + 4.0), (68.0, 13.0, 34.0), (74.0, 20.0, 30.0)]),
        dict(name="battery leads", colour="black", pts=[
            (BATT_X0 + BATT_POCKET_X, 0.0, 30.0), (158.0, 8.0, 28.0),
            (baby["x0"] + 6.0, 19.0, 26.0)]),
        dict(name="Baby VOUT -> Boost", colour="tab:green", pts=[
            (baby["x0"] + 3.0, 20.0, 20.0), (140.0, 9.0, 10.0), (100.0, 9.0, 10.0),
            (94.0, 19.0, 14.0)]),
        dict(name="aft panel harness", colour="tab:purple", pts=[
            (OUTER_L - 2.0, PANEL_CY, USB_Z), (215.0, 12.0, 28.0), (195.0, 9.0, 24.0),
            (182.0, 14.0, 22.0), (baby["x0"] + 20.0, 19.0, 20.0)]),
    ]


def insert_inventory() -> list[dict]:
    """Every heat-set insert in the model, with the axis the iron enters on.

    Written down explicitly because "can a tool actually reach this fastener"
    is not visible in any solid-geometry check, and it is what has cost the
    most bench time: the BMP581's four pilots sat behind the old static-bay
    frame with no corridor to them at all.

    `access` is where the iron comes from.  "seam" means along +Y from the open
    mating face, so the corridor from y=0 to the pilot must be clear.  "aft"
    means along -X from outside the base, which is free space by definition.
    """
    out: list[dict] = []
    for x, z in FLANGE_SCREWS:
        out.append(dict(name="flange", x=x, y=0.0, z=z, access="seam"))
    for bname, b in BOARDS.items():
        for hx, hz in board_standoffs(b):
            out.append(dict(name=f"board:{bname}", x=b["x0"] + hx, y=Y_PCB,
                            z=b["z0"] + hz, access="seam"))
    for cx, cz in cup_screws():
        out.append(dict(name="static_bay_cup", x=cx, y=Y_LAND, z=cz, access="seam"))
    for cy, cz in TAIL_SCREWS:
        out.append(dict(name="tail_panel", x=OUTER_L, y=cy, z=cz, access="aft"))
    return out


def assembly_parts() -> list[tuple[str, cq.Workplane]]:
    """The separately-printed parts, in ASSEMBLY position (not print pose).

    Used to check that none of them foul a half — the pm_tray's retaining rim
    pointed at the wall land it bolts against and overlapped it by 1 mm, which
    no check could see because the tray is not part of either half.
    """
    return [("pm_tray", build_pm_tray()),
            ("static_bay", build_static_bay_cup()),
            ("tail_panel", build_tail_panel())]


# I7 as a cheap import-time gate, so a board moved down into the rail is caught
# without waiting for the full solid build.
for _i in insert_inventory():
    if _i["access"] == "seam" and _i["y"] > 0.6:
        assert _i["z"] >= INSERT_Z_MIN - 0.05, (
            f"I7 {_i['name']} pilot at z={_i['z']:.1f} is below "
            f"INSERT_Z_MIN={INSERT_Z_MIN:.1f}; the bottom flange rail sits in "
            "the heat-set iron's corridor"
        )
        assert _i["z"] <= INSERT_Z_MAX + 0.05, (
            f"I7 {_i['name']} pilot at z={_i['z']:.1f} is above "
            f"INSERT_Z_MAX={INSERT_Z_MAX:.1f}; the top flange rail sits in "
            "the heat-set iron's corridor"
        )


# =============================================================================
# QC RENDERS
# =============================================================================
# Deliberately 2-D and analytic rather than a tessellated 3-D view: v2's iso
# PNG was an unreadable brown blob, and the questions these have to answer —
# "is the boattail real", "does that board clear the skin", "does the cluster
# fit the base" — are all answerable exactly from section_params().
def _section_polyline(x: float, inset: float = 0.0, n: int = 24) -> list[tuple[float, float]]:
    y0, y1, z0, z1, r00, r10, r11, r01 = section_params(x)
    y0, y1, z0, z1 = y0 + inset, y1 - inset, z0 + inset, z1 - inset
    rs = [max(r - inset, 0.30) for r in (r00, r10, r11, r01)]
    w, h = y1 - y0, z1 - z0
    sc = min(1.0,
             (w - MIN_FLAT) / max(rs[0] + rs[1], 1e-9),
             (w - MIN_FLAT) / max(rs[3] + rs[2], 1e-9),
             (h - MIN_FLAT) / max(rs[0] + rs[3], 1e-9),
             (h - MIN_FLAT) / max(rs[1] + rs[2], 1e-9))
    r00, r10, r11, r01 = (r * sc for r in rs)
    pts: list[tuple[float, float]] = []

    def arc(cy, cz, r, a0, a1):
        for i in range(n + 1):
            a = a0 + (a1 - a0) * i / n
            pts.append((cy + r * math.cos(a), cz + r * math.sin(a)))

    pts.append((y0 + r00, z0))
    pts.append((y1 - r10, z0))
    arc(y1 - r10, z0 + r10, r10, -math.pi / 2, 0.0)
    pts.append((y1, z1 - r11))
    arc(y1 - r11, z1 - r11, r11, 0.0, math.pi / 2)
    pts.append((y0 + r01, z1))
    arc(y0 + r01, z1 - r01, r01, math.pi / 2, math.pi)
    pts.append((y0, z0 + r00))
    arc(y0 + r00, z0 + r00, r00, math.pi, 1.5 * math.pi)
    return pts


def _tag() -> str:
    return f"pod_v3_{OUTER_L:.0f}x{OUTER_W:.0f}x{OUTER_H:.0f}"


def render_layout_png() -> str:
    """Assembly view: looking down -Y at the open right half.

    This is the sheet you work from with the pod open on the bench — the pod in
    outline, every boss you have to put an insert in, where each board lands,
    and where every hose and wire runs.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle, Rectangle

    fig, ax = plt.subplots(figsize=(16, 6.0))
    xs = [OUTER_L * i / 500 for i in range(501)]
    sp = [section_params(x) for x in xs]
    ax.plot(xs, [q[3] for q in sp], "k-", lw=1.8)
    ax.plot(xs, [q[2] for q in sp], "k-", lw=1.8)
    ax.plot(xs, [q[3] - WALL for q in sp], "0.65", lw=1.0)
    ax.plot(xs, [q[2] + WALL for q in sp], "0.65", lw=1.0)
    for zline, lab in ((INSERT_Z_MIN, None), (INSERT_Z_MAX, "insert band (flange rails)")):
        ax.axhline(zline, color="tab:red", ls=":", lw=0.9, alpha=0.55, label=lab)

    colours = {"MS4525": "tab:blue", "BOOST": "tab:orange", "BABY": "tab:green",
               "PROMICRO": "tab:purple", "BMP581": "gold", "MAG": "tab:pink"}
    for name, b in BOARDS.items():
        ax.add_patch(Rectangle((b["x0"], b["z0"]), b["L"], b["W"],
                               fc=colours[name], ec="k", alpha=0.40, lw=1.0, zorder=2))
        for hx, hz in board_standoffs(b):
            ax.add_patch(Circle((b["x0"] + hx, b["z0"] + hz), BOARD_POST_D / 2,
                                fc="white", ec="saddlebrown", lw=0.9, alpha=0.9, zorder=4))
            ax.plot(b["x0"] + hx, b["z0"] + hz, "+", color="saddlebrown", ms=4,
                    mew=0.9, zorder=5)
        ax.annotate(f"{name}  {b['L']:.0f}x{b['W']:.1f}",
                    (b["x0"] + b["L"] / 2, b["z0"] + b["W"] + 0.8),
                    ha="center", va="bottom", fontsize=7, zorder=9,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))

    ax.add_patch(Rectangle((CUP_X0, CUP_Z0), CUP_X1 - CUP_X0, CUP_Z1 - CUP_Z0,
                           fc="none", ec="k", ls="--", lw=1.2, zorder=3))
    ax.annotate("static_bay cup", (CUP_X0 + 2.0, CUP_Z0 + 1.2), fontsize=7, zorder=9,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    for cx, cz in cup_screws():
        ax.add_patch(Circle((cx, cz), BOARD_POST_D / 2, fc="white", ec="saddlebrown",
                            lw=0.9, alpha=0.9, zorder=4))
        ax.plot(cx, cz, "+", color="saddlebrown", ms=4, mew=0.9, zorder=5)
    for hx, hz in static_hole_centers():
        ax.plot(hx, hz, "x", color="k", ms=4, zorder=6)

    ax.add_patch(Rectangle((BATT_X0, BATT_Z0), BATT_POCKET_X, BATT_POCKET_Z,
                           fc="none", ec="0.35", lw=1.3, ls="-.", zorder=1))
    ax.annotate(f"battery {BATT_X:.1f} x {BATT_Y:.1f} x {BATT_Z:.1f} — on the seam, behind",
                xy=(BATT_X0 + 20, BATT_Z0), xytext=(BATT_X0 + 8, -3.6),
                fontsize=7, color="0.35", zorder=9,
                arrowprops=dict(arrowstyle="-", color="0.6", lw=0.8))

    ax.plot([SUN_TIP_X0, SUN_AFT_X], [PITOT_AXIS_Z] * 2, color="tab:blue",
            lw=9, alpha=0.35, solid_capstyle="butt", zorder=1)
    ax.annotate("ESA SUN-B", (10, PITOT_AXIS_Z - 5), fontsize=7, color="tab:blue")
    for bx in SUN_BARB_X:
        ax.plot([bx, bx], [PITOT_AXIS_Z, SUN_BARB_TIP_Z], color="tab:blue", lw=2.2, zorder=2)
    ax.plot(0, 0, alpha=0)

    for x, z in FLANGE_SCREWS:
        ax.plot(x, z, "+", color="tab:red", ms=8, mew=1.4, zorder=6)
    ax.add_patch(Rectangle((DRAIN_X0, INNER_Z0), DRAIN_X1 - DRAIN_X0,
                           DRAIN_ROOF - INNER_Z0, fc="tab:cyan", ec="k", alpha=0.5, lw=0.8))
    ax.annotate("labyrinth drain", (DRAIN_X0, DRAIN_ROOF + 1.5), fontsize=7)
    ax.plot([OUTER_L, OUTER_L], [BASE_Z0, OUTER_H], color="crimson", lw=2.5)
    ax.annotate("aft panel\n(rocker/USB/LEDs)", (OUTER_L - 3, (BASE_Z0 + OUTER_H) / 2),
                fontsize=7, color="crimson", ha="right", va="center")

    for r in build_routes():
        px = [q[0] for q in r["pts"]]
        pz = [q[2] for q in r["pts"]]
        ax.plot(px, pz, ls="--", lw=1.5, color=r["colour"], zorder=7, label=r["name"])
        ax.plot(px[0], pz[0], "o", color=r["colour"], ms=3.5, zorder=8)

    handles = [Line2D([], [], marker="o", ls="none", mfc="white", mec="saddlebrown",
                      ms=9, label=f"M2.5 insert boss (Ø{BOARD_POST_D:.0f})"),
               Line2D([], [], marker="+", ls="none", color="tab:red", ms=9,
                      label="clamshell flange screw"),
               Line2D([], [], marker="x", ls="none", color="k", ms=7,
                      label="static port")]
    leg1 = ax.legend(handles=handles, loc="upper left", fontsize=7, framealpha=0.9)
    ax.add_artist(leg1)
    ax.legend(loc="upper right", fontsize=7, ncol=2, framealpha=0.9)

    ax.set_title(f"Wing pod v3 — assembly layout, looking down -Y into the open right half"
                 f"   ({OUTER_L:.0f} x {OUTER_W:.0f} x {OUTER_H:.0f} mm)")
    ax.set_xlabel("X aft (mm)"); ax.set_ylabel("Z up (mm)")
    ax.set_aspect("equal"); ax.grid(alpha=0.25)
    ax.set_xlim(SUN_TIP_X0 - 5, OUTER_L + 14); ax.set_ylim(-5, OUTER_H + 13)
    fig.tight_layout()
    out = f"{_tag()}_layout.png"
    fig.savefig(out, dpi=130); plt.close(fig)
    return out


def render_profile_png() -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = [OUTER_L * i / 400 for i in range(401)]
    sp = [section_params(x) for x in xs]
    fig, axes = plt.subplots(3, 1, figsize=(11, 10))

    ax = axes[0]
    ax.plot(xs, [p[3] for p in sp], "k-", lw=1.6)
    ax.plot(xs, [p[2] for p in sp], "k-", lw=1.6)
    ax.axvline(NOSE_LEN, color="0.7", ls="--", lw=0.8)
    ax.axvline(MID_END_X, color="0.7", ls="--", lw=0.8)
    ax.annotate(f"nose {NOSE_LEN:.0f} ({NOSE_LEN / D_EQ:.2f} D)", (NOSE_LEN / 2, OUTER_H + 3),
                ha="center", fontsize=8)
    ax.annotate(f"boattail {TAIL_LEN:.0f} ({TAIL_LEN / D_EQ:.2f} D), max {BOATTAIL_ANGLE:.1f}deg",
                ((MID_END_X + OUTER_L) / 2, OUTER_H + 3), ha="center", fontsize=8)
    ax.plot([SUN_TIP_X0, 0], [PITOT_AXIS_Z, PITOT_AXIS_Z], color="tab:blue", lw=3,
            solid_capstyle="butt")
    ax.annotate(f"SUN-B protrudes {SUN_PROTRUDE:.0f}", (SUN_TIP_X0, PITOT_AXIS_Z - 6),
                fontsize=8, color="tab:blue")
    ax.set_title(f"Side elevation — fineness {FINENESS:.2f}, base/frontal "
                 f"{BASE_A / FRONTAL_A:.2f}   (v2 was 3.4 with a 12 mm tail)")
    ax.set_ylabel("Z up (mm)"); ax.set_aspect("equal"); ax.grid(alpha=0.3)

    ax = axes[1]
    ax.plot(xs, [p[1] for p in sp], "k-", lw=1.6)
    ax.plot(xs, [p[0] for p in sp], "k-", lw=1.6)
    ax.axhline(0.0, color="tab:red", ls=":", lw=1.0)
    ax.annotate("clamshell seam", (OUTER_L * 0.55, 1.5), color="tab:red", fontsize=8)
    ax.set_title("Plan — asymmetric about the seam (thin left cover, wide right bay)")
    ax.set_ylabel("Y right (mm)"); ax.set_aspect("equal"); ax.grid(alpha=0.3)

    ax = axes[2]
    for x, c in ((6.0, "tab:purple"), (26.0, "tab:blue"), (NOSE_LEN, "tab:green"),
                 (MID_END_X, "tab:olive"), (200.0, "tab:orange"), (OUTER_L, "tab:red")):
        p = _section_polyline(x)
        ax.plot([q[0] for q in p], [q[1] for q in p], "-", color=c, lw=1.3,
                label=f"x={x:.0f}")
    ax.set_title("Sections — one family from nose mouth to base (same 8-edge wire)")
    ax.set_xlabel("Y right (mm)"); ax.set_ylabel("Z up (mm)")
    ax.set_aspect("equal"); ax.grid(alpha=0.3); ax.legend(fontsize=7, ncol=3)

    axes[0].set_xlabel("X aft (mm)"); axes[1].set_xlabel("X aft (mm)")
    fig.tight_layout()
    out = f"{_tag()}_profile.png"
    fig.savefig(out, dpi=110); plt.close(fig)
    return out


def render_interior_png() -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle

    fig, (ax, axp) = plt.subplots(2, 1, figsize=(12, 9))
    xs = [OUTER_L * i / 400 for i in range(401)]
    for inset, style in ((0.0, "k-"), (WALL, "0.6")):
        sp = [section_params(x) for x in xs]
        ax.plot(xs, [p[3] - inset for p in sp], style, lw=1.2)
        ax.plot(xs, [p[2] + inset for p in sp], style, lw=1.2)

    colors = {"MS4525": "tab:blue", "BOOST": "tab:orange", "BABY": "tab:green",
              "PROMICRO": "tab:purple", "BMP581": "gold", "MAG": "tab:pink"}
    for name, b in BOARDS.items():
        ax.add_patch(Rectangle((b["x0"], b["z0"]), b["L"], b["W"],
                               fc=colors[name], ec="k", alpha=0.75, lw=0.8))
        ax.annotate(name, (b["x0"] + b["L"] / 2, b["z0"] + b["W"] / 2),
                    ha="center", va="center", fontsize=7)
        for hx, hz in board_standoffs(b):
            ax.add_patch(Circle((b["x0"] + hx, b["z0"] + hz), BOARD_POST_D / 2,
                                fc="none", ec="saddlebrown", lw=1.0))
    ax.add_patch(Rectangle((BAY_X0, BAY_Z0), BAY_X1 - BAY_X0, BAY_Z1 - BAY_Z0,
                           fc="none", ec="k", ls="--", lw=1.0))
    ax.add_patch(Rectangle((BATT_X0, BATT_Z0), BATT_POCKET_X, BATT_POCKET_Z,
                           fc="none", ec="k", lw=1.4))
    ax.annotate("battery 70x6x50 (laid down)", (BATT_X0 + 3, BATT_Z0 + 3), fontsize=7)
    ax.plot([SUN_TIP_X0, SUN_AFT_X], [PITOT_AXIS_Z] * 2, color="tab:blue", lw=6, alpha=0.5)
    for bx in SUN_BARB_X:
        ax.plot([bx, bx], [PITOT_AXIS_Z, SUN_BARB_TIP_Z], color="tab:blue", lw=2)
    for hx, hz in static_hole_centers():
        ax.plot(hx, hz, "kx", ms=3)
    ax.add_patch(Rectangle((DRAIN_X0, INNER_Z0), DRAIN_X1 - DRAIN_X0,
                           DRAIN_ROOF - INNER_Z0, fc="tab:cyan", ec="k", alpha=0.5, lw=0.8))
    ax.annotate("drain", (DRAIN_X0, DRAIN_ROOF + 1.5), fontsize=7)
    for x, z in FLANGE_SCREWS:
        ax.plot(x, z, "r+", ms=6)
    ax.set_title(f"Right half interior (X–Z) — SUN aft face x={SUN_AFT_X:.0f}, "
                 f"static ports {100 * STATIC_PORT_X / OUTER_L:.0f}% of length")
    ax.set_xlabel("X aft (mm)"); ax.set_ylabel("Z up (mm)")
    ax.set_aspect("equal"); ax.grid(alpha=0.3)

    sp = [section_params(x) for x in xs]
    axp.plot(xs, [p[1] for p in sp], "k-", lw=1.2)
    axp.plot(xs, [p[1] - WALL for p in sp], "0.6", lw=1.0)
    axp.axhline(Y_LAND, color="tab:brown", lw=0.9, label="wall land")
    axp.axhline(Y_PCB, color="tab:brown", ls="--", lw=0.9, label="PCB face (standoffs)")
    axp.axhline(FLANGE_W, color="tab:red", ls=":", lw=0.9, label="flange rail depth")
    axp.add_patch(Rectangle((BATT_X0, BATT_Y0), BATT_POCKET_X, BATT_POCKET_Y,
                            fc="none", ec="k", lw=1.4))
    axp.set_title("Plan (X–Y): standoffs raise PCBs off the land; battery on the seam")
    axp.set_xlabel("X aft (mm)"); axp.set_ylabel("Y right (mm)")
    axp.set_aspect("equal"); axp.grid(alpha=0.3); axp.legend(fontsize=7, ncol=4)

    fig.tight_layout()
    out = f"{_tag()}_interior.png"
    fig.savefig(out, dpi=110); plt.close(fig)
    return out


def render_aft_panel_png() -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle

    fig, ax = plt.subplots(figsize=(6.5, 8))
    for inset, style, lab in ((0.0, "k-", "base / plate outline"),
                              (WALL, "0.6", "shell inner"),
                              (WALL + TAIL_RIM_W, "0.8", "rim inner = opening")):
        p = _section_polyline(OUTER_L, inset)
        ax.plot([q[0] for q in p], [q[1] for q in p], style, lw=1.4, label=lab)
    ax.add_patch(Rectangle((PANEL_CY - SW_CUT_Y / 2, SW_Z - SW_CUT_Z / 2),
                           SW_CUT_Y, SW_CUT_Z, fc="none", ec="crimson", lw=1.5))
    ax.annotate("COM-08837 rocker\n19.6 x 13.0", (PANEL_CY, SW_Z), ha="center",
                va="center", fontsize=7, color="crimson")
    ax.add_patch(Rectangle((PANEL_CY - USB_WIN_Y / 2, USB_Z - USB_WIN_Z / 2),
                           USB_WIN_Y, USB_WIN_Z, fc="none", ec="crimson", lw=1.5))
    ax.annotate("CAB-15464", (PANEL_CY, USB_Z - 6.5), ha="center", fontsize=7, color="crimson")
    for dy in (-USB_EAR_PITCH / 2, USB_EAR_PITCH / 2):
        ax.add_patch(Circle((PANEL_CY + dy, USB_Z), M3_CLR_D / 2, fc="none", ec="crimson", lw=1.2))
    for dy, lab in ((-LED_DY, "blue !CHG!"), (LED_DY, "red VOUT")):
        ax.add_patch(Circle((PANEL_CY + dy, LED_Z), LED_HOLE_D / 2, fc="none", ec="crimson", lw=1.5))
        ax.annotate(lab, (PANEL_CY + dy, LED_Z - 7), ha="center", fontsize=6, color="crimson")
    for cy, cz in TAIL_SCREWS:
        ax.add_patch(Circle((cy, cz), BOSS_D / 2, fc="none", ec="saddlebrown", lw=1.2))
        ax.plot(cy, cz, "k.", ms=3)
    ax.axvline(0.0, color="tab:red", ls=":", lw=1.0)
    ax.annotate("seam", (0.6, BASE_Z0 + 2), color="tab:red", fontsize=7)
    ax.set_title(f"Aft service panel (looking forward)\nbase {BASE_W:.0f} x {BASE_H:.0f} mm, "
                 f"plate {PANEL_T:.1f} mm; all 4 inserts right of the seam")
    ax.set_xlabel("Y right (mm)"); ax.set_ylabel("Z up (mm)")
    ax.set_aspect("equal"); ax.grid(alpha=0.3); ax.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    out = f"{_tag()}_aft_panel.png"
    fig.savefig(out, dpi=110); plt.close(fig)
    return out


if __name__ == "__main__":
    main()
