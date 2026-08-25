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
from pathlib import Path

import cadquery as cq

# =============================================================================
# PARAMETERS (mm)
# =============================================================================

# --- shell / envelope -------------------------------------------------------
WALL = 2.5
OUTER_L = 235.0
# Clamshell INVERTED as of print-4.  The board side used to be the 42 mm half
# carrying every wall (150.7 cm^3) and the cover a bare 10 mm shell — so the
# part that changes every prototype revision was the expensive one to reprint.
# Now the deep bowl is on -Y and the boards mount on a shallow flat plate.
#
# The plate depth is NOT a free choice: WALL 2.5 + WALL_LAND_T 7.0 +
# STANDOFF_H 4.0 = 13.5 mm before the PCB face exists.  16 leaves the PCB at
# y=+2.5 and lets components hang past the seam into the bowl, as intended.
LEFT_EXTENT = 36.0   # -Y bowl: SUN cradle, battery, aft rim, drain
RIGHT_EXTENT = 16.0  # +Y flat plate: the board land, and nothing else
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

# Smallest corner arc any inset section may have.  The section topology is
# fixed at 4 lines + 4 arcs so a corner can never go sharp, and the old 0.30
# floor made the inner TOP corners degenerate: the top radii are 1.2, so at
# inset=WALL they clamped to 0.30 and the loft grew a row of 19.6 x 0.30 x
# 0.30 mm ribbon faces along the cavity's top-flank corner.  Those tessellate
# to degenerate triangles at some angular tolerances and not others -- 0.06
# and 0.09 came out watertight, 0.07, 0.08 and 0.12 did not -- so the export
# was passing or failing on luck.  A larger floor only ever ADDS material at
# an interior fillet, so it cannot thin a wall.
# Pro Micro tray stack.  The floor sits OUTBOARD of the board (the board's
# back face rests on its inboard side) and the rim reaches inboard to form the
# pocket.  Getting this backwards -- floor inboard at board_y - t -- hung the
# rim 2.9 mm across the seam into the bowl's flange rail, which is what I6
# caught as pm_tray fouling both halves.

# 2.3, not 1.5: at 1.5 the rim stood only 0.72 mm proud of the component face,
# which is a chamfer, not a pocket.  2.3 leaves 1.5 mm of lip to actually
# capture the board edge.

# MMC5983 keeper rails: how tightly the magnetometer is held against rotation.
KEEPER_CLR = 0.15      # per side, board edge to rail
KEEPER_W = 2.0         # rail thickness
KEEPER_END_CLR = 2.0   # rail stops short of the board ends

MIN_CORNER_R = 0.80
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
BASE_Y0 = -29.0   # bowl side gives up 7 mm  -> 7.6 deg
BASE_Y1 = 12.0    # plate side gives up 4 mm -> 4.4 deg
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
# 3.3, not 3.0.  At 3.0 the flange screw boss is EXACTLY tangent to the rail
# it is unioned into: boss top = 3.0 + BOSS_D/2 = 6.5, and the rail's top face
# is INNER_Z0 + FLANGE_RAIL = 6.5.  Mirrored at the top rail.  A tangent union
# touches along a line instead of crossing it, so the mesher subdivides the
# shared corner differently on each side and leaves a T-junction -- four
# odd-parity edges at y=-6.0, z=6.5 that made pod_left.stl non-watertight at
# some angular tolerances and not others.  0.3 mm of overlap makes it
# transversal.  Do not re-derive this from FLANGE_RAIL without re-checking the
# insert-reach band, which is defined from the same constant.
SHELL_SCREW_INSET = 3.3
FLANGE_SCREW_PITCH = 28.0
# (x, z) of the two screws that clamp the SUN's threaded band.  z sits above
# the bore: below it the section bottom runs out before the land does.
CLAMP_SCREWS = [(13.0, 35.0), (27.0, 35.0)]
# sun_clamp fasteners (x, z), entered along +Y from the seam into inserts in
# the bowl.  Two Z stations either side of the bore, two X stations fore and
# aft, so the cap cannot rock or slide.  Both Z values sit inside the
# insert-reach band; the obvious choice of ears at z = axis +/- (R_OUTER + 4)
# does not, because the lower ear lands at z=6.8 against a band starting at
# 10.5.

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
# Recentring the section on the pitot is what the seam move buys.  The pod's Y
# centre is now (RIGHT_EXTENT - LEFT_EXTENT)/2 = -10, and the SUN sits 5 mm
# left of that so its clamp land clears the seam and the plate stays flat.
# Nose flank growth goes from 34.7/2.7 mm to 23.7/13.7, dropping A13 from
# 41.7 deg to about 31.
PITOT_AXIS_Y = -15.0
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
# Measured 2026-08-23 (BOARD_CALIPERS.md).  These were pre-caliper estimates
# and disagreed with BOARD_SPECS by up to 0.5 mm -- the layout sized the cup and
# centred the ESP32 from one set while the envelope checks used the other.
BMP581_L, BMP581_W, BMP581_COMP = 25.31, 25.18, 3.08
BOOST_L, BOOST_W = 25.25, 25.25
PM_L, PM_W = 33.51, 17.70  # long axis along X
PM_PCB, PM_COMP = 0.78, 4.4
MAG_L, MAG_W = 19.13, 7.52
MS_L, MS_W = 22.9, 17.0
BABY_L, BABY_W = 32.90, 33.09
Y_LAND = RIGHT_EXTENT - WALL - WALL_LAND_T   # inboard face of the plate's land
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
# 2 rows x 5 cols: spread along X, the flow direction, so the array averages
# over the pressure gradient rather than sampling one station.  (Briefly 5 x 2
# to let a wide array sit further forward -- unnecessary now the bay moved.)
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

# Four sun_clamp fasteners: fore/aft on the two shoulder bulkheads, and either
# side of the bore in Z at just outside the clamp bore plus a screw radius.
_SCS_DZ = CRADLE_R_CLAMP + 2.5
# The fore pair goes on the MID bulkhead, not the nose one: at x 3.8 the
# section is barely wider than the bore, so the opener is clipped away there
# and there is no cap to bolt down.
MID_BH_X = 0.5 * (SUN_BARREL_X0 + SUN_BARB_X[0]) - 1.75
_SCS_MID_X = MID_BH_X + 1.75
SUN_CLAMP_SCREWS = [
    (_SCS_MID_X, PITOT_AXIS_Z - _SCS_DZ),
    (_SCS_MID_X, PITOT_AXIS_Z + _SCS_DZ),
    (0.5 * (AFT_BH_X0 + AFT_BH_X1), PITOT_AXIS_Z - _SCS_DZ),
    (0.5 * (AFT_BH_X0 + AFT_BH_X1), PITOT_AXIS_Z + _SCS_DZ),
]
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

BOOST_CONN_FORE = 4.0                              # Boost fore-Qwiic cable room

# Layout follows the WIRING ORDER, which is linear:
#   battery -JST- babysitter -wires- ESP32 -qwiic- BMP581 -qwiic- boost
#   -wires/JST- MS4525,  and the boost is a Y:  boost -qwiic- MMC5983.
# Print-4 ignored that and put the babysitter full aft with the ESP32 in the
# middle, a run too long for the existing loom.  Laid out fore->aft in chain
# order the runs come out short and nothing crosses.
COL_A_X0 = 44.0
# MS4525 goes ABOVE the SUN barrel (which reaches z=29.9 out to x=79);
# its inboard components will not clear the brass otherwise.
MS_X0, MS_Z0 = COL_A_X0, 32.0
# MMC5983 moved aft from 44 to shorten the boost->mag Qwiic to ~12 mm.  Capped
# so its aft cable envelope still stops short of the SUN's aft bulkhead at
# x=79.03 -- it may not straddle a full-height plate.
MAG_X0, MAG_Z0 = 55.0, LOW_ROW_Z0
COL_A_W = max(MS_L, MAG_L)

# Column B starts aft of the SUN's aft bulkhead (a full-height plate at
# x 79.0..82.5 that nothing may straddle).  The Boost's fore Qwiic needs 4 mm
# ahead of the board.
COL_B_X0 = AFT_BH_X1 + BOOST_CONN_FORE + BOARD_GAP
# Boost DROPPED from z=32.  At 32 the board spanned 32.0..57.25 while the top
# flange rail starts at 54.5, so 2.75 mm of it wanted to be inside the seal --
# which is exactly why it would not fit the printed plate -- and its upper
# mounting pair landed inside the rail too, which is why those two had to be
# demoted to nubs.  Down here all four holes are inside the insert-reach band
# (10.5..50.5) AND clear of both rails, so the Boost gets four screws.
BOOST_X0, BOOST_Z0 = COL_B_X0, 20.0
COL_B_W = BOOST_L

# --- static bay, with the ESP32 stacked on its inboard face ---------------
CUP_X0 = 122.0
BMP_X0, BMP_Z0 = CUP_X0 + CUP_WALL + CUP_CLR, 17.8
# BAY_* is the PLENUM volume (cup interior); CUP_* is the cup's outer shell.
BAY_X0, BAY_X1 = BMP_X0 - CUP_CLR, BMP_X0 + BMP581_L + CUP_CLR
BAY_Z0, BAY_Z1 = BMP_Z0 - CUP_CLR, BMP_Z0 + BMP581_W + CUP_CLR
CUP_X1 = BAY_X1 + CUP_WALL
CUP_Z0, CUP_Z1 = BAY_Z0 - CUP_WALL, BAY_Z1 + CUP_WALL
# The cup was 16.6 mm deep in Y for a board that needs 5.6, and its glands were
# cut at the deep end where the BMP581 has nothing -- its two Qwiic connectors
# sit at the BOARD plane, so a gland down there could never have taken a cable.
# Sized to the board now: interior clears the components by ~1.4 mm.
CUP_Y0 = Y_PCB - PCB_T - BMP581_COMP - CUP_WALL - 1.4
BAY_Y0 = CUP_Y0 + CUP_WALL

# The ESP32 rides on the cup's outboard-facing back, not on the plate: that is
# what lets it sit beside the babysitter in the chain without spending 33 mm of
# its own X.  Back face on the cup, components into the open bowl.
PM_X0 = 0.5 * (CUP_X0 + CUP_X1) - PM_L / 2
PM_Z0 = 0.5 * (CUP_Z0 + CUP_Z1) - PM_W / 2
# Standing off the cup, not flush on it: the board needs posts to mount to,
# and a keepout that reaches 1 mm behind the back face would otherwise sit
# inside the sealed plenum wall.
# 1.0 is enough: the measured back-face height is 0.00, so this is solder-
# clearance only.  The whole stack (standoff + PCB + components + cover) ends
# 1.7 mm short of the battery, and every mm here comes straight off that gap.
PM_STANDOFF = 1.0
PM_Y = CUP_Y0 - PM_STANDOFF
# Inner face of the cover's roof: past the tallest component with clearance.
PM_COVER_CLR = 0.6
PM_COVER_T = 2.0
# Outer face of the cover roof; the cup's bosses run out to meet it.
PM_COVER_Y = PM_Y - PM_PCB - PM_COMP - PM_COVER_CLR - PM_COVER_T

BABY_X0, BABY_Z0 = 162.0, 12.0                     # Babysitter, beside the ESP32

# Battery: laid down on the seam, clear of the SUN aft bulkhead.
BATT_X0 = AFT_BH_X1 + 2.5
# In the bowl, not straddling the seam.  That is what removed the flange-rail
# clearance problem; the pocket no longer notches the sealing land at all.
BATT_Y0 = -24.0
BATT_Z0 = (OUTER_H - BATT_POCKET_Z) / 2

# Static ports sit mid-body, where a side port reads closest to freestream.
# Centred in the plenum again.  It was briefly biased to the forward wall to
# chase L3's 40-60% band, which left the array 4.5 mm from the fore wall and
# 20.4 from the aft -- visibly wrong in the printed part, and the wrong lever:
# the band is a property of where the BAY sits, not of where the holes sit
# inside it.  With the bay at x 122 the centred array lands at 59%.
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
    PITOT_AXIS_Y - _MR, PITOT_AXIS_Y + _MR, PITOT_AXIS_Z - _MR, PITOT_AXIS_Z + _MR,
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
    rs = [max(r - inset, MIN_CORNER_R) for r in (r00, r10, r11, r01)]
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
    r10, r11 = max(r10 - inset, MIN_CORNER_R), max(r11 - inset, MIN_CORNER_R)
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
    r00, r01 = max(r00 - inset, MIN_CORNER_R), max(r01 - inset, MIN_CORNER_R)
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
assert PITOT_AXIS_Y + CLAMP_R_OUTER < -0.5, (
    f"A14 SUN clamp land reaches y={PITOT_AXIS_Y + CLAMP_R_OUTER:.2f}, crossing the "
    "seam — the board plate would need a cradle bulge and would not be flat"
)
assert NOSE_LIP_WALL >= 1.5, "P7 nose lip below the 1.5 mm that survived print-1"
assert CLAMP_CLEAR >= 0.20, "L2 clamp clearance below the print-1 slip fit"
assert SUN_RECESS_DEPTH - SUN_RECESS_BOSS_LEN >= 2.5, (
    "P5 locating boss must leave >=2.5 mm of the cup unused"
)
assert SUN_BARB_TIP_Z + 8.0 < INNER_Z1, (
    f"L3 barb tips z={SUN_BARB_TIP_Z:.1f} leave no hose room under z={INNER_Z1:.1f}"
)
def sun_obstacles() -> list[tuple[float, float, float, float, float, float]]:
    """What the SUN and its cradle actually occupy, as (x0,x1,y0,y1,z0,z1).

    The old check compared a board's inboard reach against CRADLE_LAND_Y on the
    assumption the cradle straddled the seam.  With the pitot moved off the
    seam into the bowl that is the wrong question: the cradle is a set of
    discrete plates at known x, with open barrel between them, so a board only
    has to clear what is actually there at its own x and z.
    """
    out = []
    # The clamp land IS local to the bore.  The two shoulder bulkheads are not:
    # _cradle_plate boxes them over the FULL height and clips to the envelope,
    # because their only attachment is the top and bottom skin -- their Y band
    # never reaches the outer wall.  Modelling them as discs understated them
    # and let boards be placed straddling a solid plate.
    out.append((SUN_THREAD_X0, SUN_THREAD_X1,
                PITOT_AXIS_Y - CLAMP_R_OUTER, PITOT_AXIS_Y + CLAMP_R_OUTER,
                PITOT_AXIS_Z - CLAMP_R_OUTER, PITOT_AXIS_Z + CLAMP_R_OUTER))
    for x0, x1 in ((NOSE_BH_X0, NOSE_BH_X1), (AFT_BH_X0, AFT_BH_X1)):
        out.append((x0, x1,
                    PITOT_AXIS_Y - CRADLE_LAND_Y, PITOT_AXIS_Y + CRADLE_LAND_Y,
                    0.0, OUTER_H))
    # the brass itself, everywhere along its length
    br = SUN_BARREL_OD / 2 + 1.0
    out.append((SUN_TIP_X0, AFT_BH_X1, PITOT_AXIS_Y - br, PITOT_AXIS_Y + br,
                PITOT_AXIS_Z - br, PITOT_AXIS_Z + br))
    # barbs stand up from the axis
    bx = SUN_BARB_STEM_OD / 2 + 2.0
    out.append((SUN_BARB_X[0] - bx, SUN_BARB_X[2] + bx,
                PITOT_AXIS_Y - bx, PITOT_AXIS_Y + bx,
                PITOT_AXIS_Z, SUN_BARB_TIP_Z + 2.0))
    return out
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
# --- board specs, measured 2026-08-23 (BOARD_CALIPERS.md) -------------------
# Every value here came off the actual boards.  The public documentation gives
# no connector positions and no hole positions for any of them.
#
#   edge   fore (-X) / aft (+X) / up (+Z) / down (-Z) / face (-Y, off the PCB)
#   at     distance along that edge from its low end.  A fore/aft edge runs in
#          v so `at` is a v value; an up/down edge runs in u so it is a u value.
#   need   how far the MATED cable runs before it can bend.
#   when   "always"  -> reserves envelope inside the closed pod
#          "service" -> only mated with the plate off, so it reserves nothing.
#          The Pro Micro's USB-C is a service port (flash with the cover off);
#          the Babysitter's USB-B has the panel pigtail permanently in it.
#   nubs   support-only posts, no insert and no screw, for boards with too few
#          holes to sit flat.
#   keepers  ribs capturing the board in v so it cannot rotate — the MMC5983
#          has a single hole and its orientation IS the measurement.
BOARD_SPECS: dict[str, dict] = {
    "MS4525": dict(
        L=22.9, W=17.0, pcb=1.6, comp=9.9, back=1.6,
        holes=[(2.50, 14.50), (20.40, 2.50)],
        conns=[dict(name="pitot", edge="fore", at=11.00, w=3.5, need=7.0),
               dict(name="static", edge="fore", at=5.00, w=3.5, need=7.0),
               dict(name="jst", edge="aft", at=11.50, w=6.1, need=5.0)],
    ),
    "BOOST": dict(
        L=25.25, W=25.25, pcb=1.6, comp=3.0, back=0.4,
        holes=[(2.50, 2.50), (2.50, 22.75), (22.75, 2.50), (22.75, 22.75)],
        conns=[dict(name="qwiic1", edge="fore", at=12.63, w=6.0, need=4.0),
               dict(name="qwiic2", edge="aft", at=12.63, w=6.0, need=4.0),
               # soldered wires, not a connector: they leave straight off the
               # component face, so they cost Y depth rather than X/Z area
               dict(name="vin_vout", edge="face", at=12.63, w=6.0, need=4.0)],
    ),
    "PROMICRO": dict(
        L=33.51, W=17.70, pcb=0.78, comp=4.4, back=0.0, tray=True,
        holes=[],                       # castellated edges, no mounting holes
        edge_clear=2.0,                 # room to solder a castellated pad
        # The board has no holes, so the TRAY carries the fasteners.  Three,
        # not two: two in a line still lets the tray pivot about that line.
        # Two on a fore ear, one on the upper edge at mid-length.  The obvious
        # third position -- an aft ear -- puts a post at x=127.0, which is
        # exactly the static bay's forward wall, and it would foul the USB-C
        # besides.  All three clear the insert-reach band, which starts at
        # z=10.5 while the Pro Micro has to sit at z=3.
        # The third ear is at the AFT end, not mid-length: at mid-length its
        # 4 mm boss sits under the Boost, and the Boost cannot move up.
        # No fasteners of its own, and none on the plate: the board rides on
        # four pads on the static bay's back face and is trapped by pm_cover,
        # whose four screws go into the CUP.
        cover=True,
        conns=[dict(name="usb_c", edge="aft", at=8.85, w=9.0, need=30.0,
                    when="service"),
               dict(name="qwiic", edge="up", at=0.00, w=2.6, need=7.0)],
    ),
    "BABY": dict(
        L=32.90, W=33.09, pcb=1.6, comp=5.52, back=0.5,
        holes=[(2.60, 2.60), (2.60, 30.49), (30.30, 2.60), (30.30, 30.49)],
        conns=[dict(name="batt_jst", edge="up", at=19.20, w=8.0, need=4.0),
               dict(name="load", edge="fore", at=16.55, w=9.2, need=4.0),
               dict(name="usb_b", edge="aft", at=8.80, w=7.95, need=25.0)],
    ),
    "BMP581": dict(
        L=25.31, W=25.18, pcb=1.54, comp=3.08, back=0.0, bay=True,
        holes=[(2.55, 2.55), (22.76, 2.55)],
        nubs=[(2.55, 22.63), (22.76, 22.63)],   # both holes are on the down edge
        conns=[dict(name="qwiic1", edge="fore", at=12.59, w=5.96, need=4.0),
               dict(name="qwiic2", edge="aft", at=12.59, w=5.96, need=4.0)],
    ),
    "MAG": dict(
        L=19.13, W=7.52, pcb=1.63, comp=3.02, back=0.0,
        holes=[(2.53, 3.66)],
        nubs=[(16.60, 3.66)],                    # one hole only
        keepers=True,                            # ...and orientation is the measurement
        conns=[dict(name="qwiic", edge="aft", at=3.76, w=5.96, need=4.0)],
    ),
}


# Where each board sits: (x0, z0) on the plate's land, and optionally its own
# PCB-face Y.  Per-board Y is what lets one board's connector pass under a
# neighbour's edge instead of fighting it for the same X.
BOARD_PLACEMENT: dict[str, dict] = {
    "MS4525":   dict(x0=MS_X0, z0=MS_Z0),
    "MAG":      dict(x0=MAG_X0, z0=MAG_Z0),
    "BOOST":    dict(x0=BOOST_X0, z0=BOOST_Z0),
    # y=3.5 (the outboard limit, Y_LAND - STANDOFF_H) rather than the default
    # 2.5.  The tray hangs a floor outboard of the board and a rim inboard of
    # it, so it needs the depth: at 2.5 the rim came within 0.20 mm of the
    # seam.  Out here it clears by 1.2 mm, and the components' inboard reach
    # drops to -1.68, which also clears the SUN's aft bulkhead top at -1.84.
    # Rides on the static bay's back face, not on the plate's land.
    "PROMICRO": dict(x0=PM_X0, z0=PM_Z0, y=PM_Y),
    "BABY":     dict(x0=BABY_X0, z0=BABY_Z0),
    "BMP581":   dict(x0=BMP_X0, z0=BMP_Z0),
}

BOARDS: dict[str, dict] = {
    name: {**BOARD_SPECS[name], **place} for name, place in BOARD_PLACEMENT.items()
}


def board_y(b: dict) -> float:
    """PCB face Y for this board.  Defaults to Y_PCB; a board may sit further
    out (shorter standoff) or further in to clear a neighbour's cable."""
    return b.get("y") or Y_PCB


def _insert_reachable(b: dict, v: float) -> bool:
    return INSERT_Z_MIN <= b["z0"] + v <= INSERT_Z_MAX


def board_standoffs(b: dict) -> list[tuple[float, float]]:
    """Screw positions — the board's REAL holes, nothing invented (I9), and
    only the ones a heat-set iron can actually reach (I7).

    A measured hole whose world Z lands outside the insert band is not a
    fastener: the flange rail is inside the iron's corridor there.  Rather
    than let that veto a placement, demote it to a nub.  The Boost lands this
    way — lower pair screwed, upper pair supported — which is the same
    two-screws-plus-two-nubs pattern the BMP581 already uses."""
    return [(u, v) for (u, v) in b["holes"] if _insert_reachable(b, v)]


def board_tray_screws(b: dict) -> list[tuple[float, float]]:
    """Fixings belonging to a board's TRAY rather than to the board itself."""
    return list(b.get("tray_screws", []))


def board_nubs(b: dict) -> list[tuple[float, float]]:
    """Support-only posts: no insert, no screw.  For boards with too few holes
    to sit flat — the BMP581 has both holes on one edge, the MMC5983 has one —
    plus any measured hole board_standoffs had to demote."""
    return list(b.get("nubs", [])) + [
        (u, v) for (u, v) in b["holes"] if not _insert_reachable(b, v)
    ]


def hose_obstacles() -> list[tuple[float, float, float, float, float, float]]:
    """The pitot/static tubing run, as (x0,x1,y0,y1,z0,z1).

    Two silicone lines leave the SUN's barbs pointing up out of the barrel and
    turn aft into the MS4525's fore ports.  That run is the single largest
    consumer of bowl volume and nothing in the model reserved it, so the space
    read as free and would have been packed.  Boxed as the hull of both
    endpoints, inflated by a tube diameter each way for the bend: silicone of
    this size will not turn inside about 3x OD, and a kinked static line reads
    as a pressure error, not as a fit problem.
    """
    bend = 3.0 * MS_TUBE_OD
    ms = BOARDS["MS4525"]
    port_z = [ms["z0"] + c["at"] for c in ms["conns"] if c["edge"] == "fore"]
    # both barbs matter, and the aft one sits BEHIND the MS4525's aft edge, so
    # its line has to come forward past the board.  Capped at the aft bulkhead,
    # which the hose cannot pass through in any case.
    x0 = min(SUN_BARB_X) - SUN_BARB_STEM_OD / 2 - bend
    x1 = min(max(max(SUN_BARB_X) + SUN_BARB_STEM_OD / 2 + bend, ms["x0"]),
             AFT_BH_X0)
    z0 = min(min(port_z), PITOT_AXIS_Z) - MS_TUBE_OD
    z1 = max(max(port_z), SUN_BARB_TIP_Z) + bend
    y0 = min(PITOT_AXIS_Y, board_y(ms) - PCB_T - ms["comp"]) - MS_TUBE_OD
    y1 = board_y(ms)
    return [(x0, x1, y0, y1, z0, z1)]


def rail_obstacles(side: int = 1) -> list[tuple[float, float, float, float, float, float]]:
    """The mating flange rails, as (x0,x1,y0,y1,z0,z1).

    These were never an I6' obstacle, and they are the reason the Boost did not
    fit the printed plate: its board spans z 32.0..57.25 while the top rail
    starts at 54.5, so 2.75 mm of board wanted to be inside the seal.  Its two
    upper posts were worse -- demoted to nubs precisely BECAUSE they sit above
    the insert-reach band, which is defined from FLANGE_RAIL, so the model knew
    the rail was there and still put the posts in it.

    Boards live on the plate, so side>0 by default.
    """
    y0, y1 = (0.0, FLANGE_W) if side > 0 else (-FLANGE_W, 0.0)
    return [
        (FLANGE_X0, FLANGE_X1, y0, y1, INNER_Z0, INNER_Z0 + FLANGE_RAIL),
        (FLANGE_X0, FLANGE_X1, y0, y1, INNER_Z1 - FLANGE_RAIL, INNER_Z1),
    ]


def cup_obstacles() -> list[tuple[float, float, float, float, float, float]]:
    """The static bay's four walls, as (x0,x1,y0,y1,z0,z1).

    The cup was never an I6' obstacle, which is how the Pro Micro came to end
    3 mm inside its forward wall while the envelope check reported clear: the
    only thing the check knew about the bay was the BMP581 sitting in its
    plenum, and that board is correctly clear of everything.  Its interior is
    excluded, so the BMP581 itself must be skipped by the caller.
    """
    y0, y1 = CUP_Y0, Y_LAND
    return [
        (CUP_X0, BAY_X0, y0, y1, CUP_Z0, CUP_Z1),   # fore wall
        (BAY_X1, CUP_X1, y0, y1, CUP_Z0, CUP_Z1),   # aft wall
        (CUP_X0, CUP_X1, y0, y1, CUP_Z0, BAY_Z0),   # lower wall
        (CUP_X0, CUP_X1, y0, y1, BAY_Z1, CUP_Z1),   # upper wall
    ]


def board_keepout(b: dict) -> tuple[float, float, float, float, float, float]:
    """3-D keepout (I6): PCB plus everything hanging off it, in world coords."""
    y = board_y(b)
    return (b["x0"], b["x0"] + b["L"],
            y - b["pcb"] - b["comp"], y + b.get("back", 0.0) + 1.0,
            b["z0"], b["z0"] + b["W"])


def board_envelope(b: dict, service: bool = False) -> list[tuple[float, ...]]:
    """Keepout PLUS a box per connector for the room its mated cable needs.

    This, not the footprint, is what must stay clear of neighbours.  The board
    model had no way to say "a cable comes out here", which is exactly why
    print-3 could not be wired.  Connectors marked when="service" are only
    mated with the plate off, so they reserve nothing inside the closed pod
    unless `service` is set.
    """
    x0, x1, y0, y1, z0, z1 = board_keepout(b)
    out = [(x0, x1, y0, y1, z0, z1)]
    for c in b.get("conns", []):
        if c.get("when", "always") == "service" and not service:
            continue
        need, half = c["need"], c["w"] / 2.0
        if c["edge"] in ("fore", "aft"):
            cz = b["z0"] + c["at"]
            ex0, ex1 = ((x0 - need, x0) if c["edge"] == "fore" else (x1, x1 + need))
            out.append((ex0, ex1, y0, y1, cz - half, cz + half))
        elif c["edge"] == "face":
            # Wires leaving perpendicular to the PCB.  These deepen the board in
            # Y instead of reaching into a neighbour's X/Z, which is cheap now
            # that the measured component heights turned out far lower than the
            # model assumed.  Conservatively taken over the whole footprint.
            out.append((x0, x1, y0 - need, y0, z0, z1))
        else:
            cx = b["x0"] + c["at"]
            ez0, ez1 = ((z0 - need, z0) if c["edge"] == "down" else (z1, z1 + need))
            out.append((cx - half, cx + half, y0, y1, ez0, ez1))
    return out


def board_keepout(b: dict) -> tuple[float, float, float, float, float, float]:
    """3-D keepout (I6): PCB plus everything hanging off it, in world coords."""
    y = board_y(b)
    # b["pcb"], not the global PCB_T.  Every board's thickness was measured;
    # using a 1.6 default modelled the Pro Micro's 0.78 mm board as twice its
    # thickness and charged it 0.8 mm of inboard depth it does not use -- on
    # the one board where depth is tightest, because its tray adds a floor
    # outboard and a rim inboard.
    return (b["x0"], b["x0"] + b["L"],
            y - b.get("pcb", PCB_T) - b["comp"], y + 1.0,
            b["z0"], b["z0"] + b["W"])


# I8: every board is screwed into inserts — an empty hole list is how v2's
# wall-mount rework silently dropped the Pro Micro's fasteners.
# I8: every board must be RESTRAINED — not merely screwed.  The old rule was
# "at least two holes", which existed to catch a board whose fasteners had been
# forgotten.  With the holes now measured that rule is wrong: the MMC5983 has
# one hole and the Pro Micro has none, and no amount of asserting will give
# them more.  What matters is that nothing can rotate or rock.
for _n, _b in BOARDS.items():
    _h, _nb = _b["holes"], _b.get("nubs", [])
    _collinear = len(_h) >= 2 and (len({round(u, 2) for u, _ in _h}) == 1
                                   or len({round(v, 2) for _, v in _h}) == 1)
    if not _h:
        assert _b.get("tray"), (
            f"I8 {_n} has no mounting holes and no tray — nothing holds it"
        )
    elif len(_h) == 1:
        assert _nb and _b.get("keepers"), (
            f"I8 {_n} has a single hole: it needs a support nub AND keepers, "
            "or it will pivot about the one screw"
        )
    elif _collinear:
        assert _nb, (
            f"I8 {_n} has {len(_h)} holes but they are collinear — it needs "
            "support nubs off that line or it will rock"
        )
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
# I6: neighbours must not collide.  In all THREE axes -- this used to test x
# and z only, on the unstated assumption that every board lies coplanar on the
# land, so separation had to come from the board plane.  The ESP32 now rides on
# the static bay's back, stacked in Y over the BMP581, and two boards that
# share x and z but sit 5 mm apart in Y are a legitimate arrangement, not a
# collision.
_names = list(BOARDS)
for _i in range(len(_names)):
    for _j in range(_i + 1, len(_names)):
        _a, _bk = board_keepout(BOARDS[_names[_i]]), board_keepout(BOARDS[_names[_j]])
        _ox = min(_a[1], _bk[1]) - max(_a[0], _bk[0])
        _oy = min(_a[3], _bk[3]) - max(_a[2], _bk[2])
        _oz = min(_a[5], _bk[5]) - max(_a[4], _bk[4])
        assert _ox <= 0 or _oy <= 0 or _oz <= 0, (
            f"I6 {_names[_i]}/{_names[_j]} keepouts overlap "
            f"({_ox:.1f} x {_oy:.1f} x {_oz:.1f} mm)"
        )

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
def _real_solid(part: cq.Workplane) -> bool:
    """Does this workplane hold an actual positive-volume solid?

    OCCT booleans do not degrade gracefully with degenerate operands, and this
    is the third time it has cost a print.  A negative-volume loft turned a cut
    into an add; a tangent operand made intersect return nothing; and here an
    EMPTY operand made intersect return the *other* shape — a cradle clamp that
    belongs entirely in the bowl was unioned into the flat plate, 13.3 cm^3 of
    it at y -28 in a half that starts at y=0.  Test before chaining.
    """
    try:
        sols = part.val().Solids()
    except Exception:
        return False
    return bool(sols) and sum(s.Volume() for s in sols) > 1e-3


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
    # The cradle lives in the bowl now, centred on the pitot axis rather than
    # split about the seam, so both halves take the same Y band and the plate
    # simply gets nothing (the clamp land clears the seam by design — A14).
    ylo, yhi = PITOT_AXIS_Y - CRADLE_LAND_Y, PITOT_AXIS_Y + CRADLE_LAND_Y
    blank = (
        cq.Workplane("XY")
        .transformed(offset=(x0, ylo, 0.0))
        .box(xlen, yhi - ylo, OUTER_H, centered=(False, False, False))
    )
    return blank.intersect(full_body_solid(0.0)).intersect(_keep_half(side))


def _sun_bore(x0: float, x1: float, r: float) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .transformed(offset=(x0, PITOT_AXIS_Y, PITOT_AXIS_Z), rotate=(0, 90, 0))
        .circle(r)
        .extrude(x1 - x0)
    )


def sun_opener() -> cq.Workplane:
    """The volume removed from the cradle so the SUN can be dropped in.

    Inverting the clamshell moved the whole cradle into the bowl, and with it
    a problem nothing checked: the bores are full circles, so the bowl wraps
    the barrel through 360 degrees and the SUN is captured.  It cannot drop in
    laterally, and it cannot slide in from the nose either -- the barbs stand
    up off the barrel and the nose bulkhead sits forward of the barb bay, so
    they have nothing to pass through.  Cutting the cradle open toward the
    seam turns it into a saddle; build_sun_clamp is the cap that closes it.
    """
    r = CLAMP_R_OUTER + 1.0
    # Stop at the top of the cradle land.  Running it up to the seam would cut
    # shell and board land that is not cradle at all -- the plates only ever
    # existed between the axis and PITOT_AXIS_Y + CRADLE_LAND_Y.
    # Overshoot the cradle plates' top face by 2 mm.  Stopping exactly on it
    # -- PITOT_AXIS_Y + CRADLE_LAND_Y is both the opener's top and the plates'
    # top -- is a coplanar cut, and it tessellated to the same kind of
    # zero-area sliver the board land produced at z=58.5.  The overshoot costs
    # nothing: above the plates there is only cavity, and the whole opener is
    # clipped to full_body_solid(WALL) so it can never reach the skin.
    box = _box(NOSE_BH_X0, PITOT_AXIS_Y, PITOT_AXIS_Z - r,
               AFT_BH_X1 - NOSE_BH_X0, CRADLE_LAND_Y + 2.0, 2 * r)
    # Clip to the CAVITY.  The nose bulkhead sits at x 3.8, where the section
    # is barely wider than the bore, so an unclipped opener walks straight out
    # through the nose skin -- the same way the barb bay once left 0.32 mm of
    # PETG and printed as a slit across the seam.  Nothing is lost by it: the
    # SUN's tip goes through the nose mouth first and the barrel then drops
    # into the saddle behind it.
    # Protect the two nose flange bosses, exactly as add_battery_pocket does.
    # They stand at z 31.5..38.5, above the thread bore's top at 30.1, so the
    # SUN still drops in underneath them and the barbs start ~13 mm further
    # aft.  Without this the opener eats the cradle land they are standing on
    # and each boss comes away as a loose 59 mm^3 block -- attached to nothing,
    # in a half that is otherwise a perfect single solid.
    for sx, sz in CLAMP_SCREWS:
        box = box.cut(_cyl_y(sx, sz, PITOT_AXIS_Y - 1.0,
                             CRADLE_LAND_Y + 3.0, BOSS_D / 2))
    return box.intersect(full_body_solid(WALL))


def build_sun_clamp() -> cq.Workplane:
    """The cap that closes the SUN saddle (sun_clamp.stl).

    Exactly the material add_pitot_cradle would have built above the pitot
    axis, so cap and saddle share the bore they were cut from and the brass is
    gripped on its own features rather than on a printed guess.
    """
    # Built from the CRADLE PLATES, not from a solid envelope.  Taking the
    # complement of a solid body gave a continuous half-tube spanning the whole
    # cradle, which is not what add_pitot_cradle removes -- between the
    # bulkheads the barrel runs through open cavity -- and that extra material
    # sat squarely in the pitot/static hose corridor.
    plates = None
    for x0, xl in ((NOSE_BH_X0, SHOULDER_BH_T),
                   (SUN_THREAD_X0, SUN_THREAD_X1 - SUN_THREAD_X0),
                   (MID_BH_X, 3.5),
                   (AFT_BH_X0, SHOULDER_BH_T)):
        pl = _cradle_plate(x0, xl, -1)
        if not _real_solid(pl):
            continue
        plates = pl if plates is None else plates.union(pl)
    # the clamp land is thicker than the plate around the threaded band
    clamp_base = _cradle_plate(SUN_THREAD_X0, SUN_THREAD_X1 - SUN_THREAD_X0, -1)
    if _real_solid(clamp_base):
        extra = clamp_base.intersect(
            _sun_bore(SUN_THREAD_X0 - 1.0, SUN_THREAD_X1 + 1.0, CLAMP_R_OUTER))
        if _real_solid(extra):
            plates = extra if plates is None else plates.union(extra)
    # Spine tying the four bulkhead caps into ONE part.  Without it the cap
    # comes out as three loose slivers, which is three things to align around
    # a piece of brass while holding the pod open.  It threads through the one
    # band that is free: below the barrel (which starts at z 17.1) and below
    # the hose corridor (which starts at z 20.5), outboard of the MMC5983.
    spine = _box(NOSE_BH_X0, PITOT_AXIS_Y, 11.0,
                 AFT_BH_X1 - NOSE_BH_X0, CRADLE_LAND_Y - 6.5 + 1.0, 5.5)
    plates = plates.union(spine)
    body = plates.intersect(sun_opener())
    body = body.cut(_sun_bore(-1.0, NOSE_BH_X1, CRADLE_R_SMOOTH))
    body = body.cut(_sun_bore(NOSE_BH_X1, SUN_THREAD_X1, CRADLE_R_CLAMP))
    body = body.cut(_sun_bore(SUN_THREAD_X1, AFT_BH_X0, CRADLE_R_BARREL))
    for x, z in SUN_CLAMP_SCREWS:
        body = body.cut(_cyl_y(x, z, PITOT_AXIS_Y - 1.0,
                               CRADLE_LAND_Y + 2.0, LID_SCREW_D / 2))
    return body


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
    clamp_base = _cradle_plate(SUN_THREAD_X0, SUN_THREAD_X1 - SUN_THREAD_X0, side)
    if _real_solid(clamp_base):
        body = _union_if_solid(body, clamp_base.intersect(
            _sun_bore(SUN_THREAD_X0 - 1.0, SUN_THREAD_X1 + 1.0, CLAMP_R_OUTER)))
    # Two barrel bulkheads + the aft bulkhead carrying the locating boss.
    # ONE barrel bulkhead, not two.  The second sat at x 60.3..63.8, directly
    # across the heat-set corridor for MS4525's and MAG's aft pilots (I7), and
    # a cradle plate spans the full Y depth of the cradle land so no approach
    # angle gets past it.  The SUN is already held by the nose bulkhead, a
    # 25 mm clamp on the thread, this bulkhead and the aft bulkhead + boss;
    # the free span it leaves is 46 mm of Ø11.71 brass.
    body = _union_if_solid(body, _cradle_plate(MID_BH_X, 3.5, side))
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
                             PITOT_AXIS_Y - barb_r, PITOT_AXIS_Z))
        .box((SUN_BARB_X[2] + barb_r + 2.0) - (SUN_BARB_X[0] - barb_r - 2.0),
             2 * barb_r, min(SUN_BARB_TIP_Z + 8.0, INNER_Z1) - PITOT_AXIS_Z,
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


def open_sun_saddle(body: cq.Workplane) -> cq.Workplane:
    """Cut the cradle open toward the seam and pilot the cap's fasteners."""
    body = body.cut(sun_opener())
    for x, z in SUN_CLAMP_SCREWS:
        # Heat-set pilot entered from the split plane, running into the
        # saddle.  Not insert_stack_cuts: that enters from the seam, which
        # would put the insert up in the board land rather than in the
        # shoulder the cap actually lands on.
        body = body.cut(_cyl_y(x, z, PITOT_AXIS_Y - INSERT_LEN - 1.5,
                               INSERT_LEN + 1.5, INSERT_OD / 2))
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
    # Keep the land clear of the inner skin PLANES by half a millimetre.  The
    # clamp used to be INNER_Z0 / INNER_Z1 exactly, which was invisible until
    # the print-4 re-pack pushed a board high enough for the clamp to bite:
    # the land's top face then landed precisely on the inner skin at z=58.5
    # and tessellated to a zero-area sliver, five boundary edges wide, in an
    # otherwise perfect single solid.  A coplanar face is not a modelling
    # error the solid kernel will complain about -- only the mesh shows it.
    lz0 = max(min(zs) - 4.0, INNER_Z0 + 0.5)
    lz1 = min(max(zs) + 4.0, INNER_Z1 - 0.5)
    land = _box(min(xs) - 4.0, Y_LAND, lz0,
                (max(xs) + 4.0) - (min(xs) - 4.0), WALL_LAND_T + 6.0,
                lz1 - lz0)
    body = _union_if_solid(body, land.intersect(full_body_solid(0.0)))

    for b in BOARDS.values():
        y = board_y(b)
        # Screwed standoffs and support-only nubs are the same post; only the
        # nub has no insert bored into it (I8).
        for hx, hz in board_standoffs(b) + board_nubs(b):
            x, z = b["x0"] + hx, b["z0"] + hz
            post = _cyl_y(x, z, y, Y_LAND + 3.0 - y, BOARD_POST_D / 2)
            body = _union_if_solid(body, post.intersect(full_body_solid(0.0)))
        # Keepers: posts flanking the board in Z at its far end.  The MMC5983
        # has a single hole and is the magnetometer, so a board free to swing
        # about that screw is a heading error, not a rattle.  They rise past
        # the PCB's back face so the edge is trapped between them.
        if b.get("keepers"):
            # RAILS, not posts.  Two cylinders flanking the board gave point
            # contact with 0.3 mm of slack a side: measured +/-2.73 deg of
            # rotation about the single screw, which on the magnetometer is a
            # heading error, not a rattle.  Straight rails hugging both long
            # edges over most of the length cut that to a few tenths, and they
            # bear along a line instead of at a point.
            ky = y - b.get("pcb", PCB_T) - 0.5
            kx0 = b["x0"] + KEEPER_END_CLR
            klen = b["L"] - 2 * KEEPER_END_CLR
            for kz in (b["z0"] - KEEPER_CLR - KEEPER_W,
                       b["z0"] + b["W"] + KEEPER_CLR):
                rail = _box(kx0, ky, kz, klen, Y_LAND + 3.0 - ky, KEEPER_W)
                body = _union_if_solid(body, rail.intersect(full_body_solid(0.0)))
    for b in BOARDS.values():
        for hx, hz in board_standoffs(b) + board_tray_screws(b):
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
TAIL_SCREWS = [(-20.0, 12.0), (-8.0, 12.0), (-20.0, 56.0), (-8.0, 56.0)]

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
    assert _cy + BOSS_D / 2 < -0.5, (
        f"I3 aft-panel boss at y={_cy} straddles the seam (it runs along X); "
        "with the plate now the shallow half these belong in the bowl"
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
    # A boss belongs to whichever half its y lies in.  Hard-coding side>0 was
    # fine while the plate was the deep half; now the aft screws are all in the
    # bowl, and four bosses were being built into the plate where they had
    # nothing to attach to (P1 caught them as 997 mm^3 of loose solids).
    #
    # The guard that fixed that left this whole block indented UNDER the `if`,
    # after the `continue` -- dead code for every screw, so no boss and no
    # pilot was built in either half and the service plate bolted into solid
    # rim.  P12 caught it; nothing else could, because a missing hole looks
    # exactly like a part that was never drilled.
    for cy, cz in TAIL_SCREWS:
        if (cy > 0) != (side > 0):
            continue
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
    # Fasteners LAST.  Every step above may union material, and add_pitot_cradle
    # unions the clamp land over exactly where the clamp screws go: cut first
    # and the holes are simply filled back in.  Print-3 showed the boss on the
    # seam side with no hole through the clamp at all.  Cut all holes after all
    # material exists and the ordering cannot bite again.
    for fn, args in ((add_pitot_cradle, (1,)), (add_battery_pocket, ()),
                     (add_electronics_wall, ()), (add_static_bay, ()),
                     (add_tail_rim, (1,)), (add_drain, ()),
                     (add_flange_fasteners, (1,))):
        body = _step(body, fn, *args)
    return body


def _build_left() -> cq.Workplane:
    body = hollow_half(-1)
    # Same reason as the right half: the cradle's clamp land was refilling the
    # clearance hole and counterbore, leaving only a divot on the outer face.
    for fn, args in ((add_pitot_cradle, (-1,)), (add_battery_pocket, ()),
                     (add_tail_rim, (-1,)), (add_flange_fasteners, (-1,)),
                     (open_sun_saddle, ())):
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


def pm_support_pads() -> list[tuple[float, float]]:
    """Where the ESP32's bare back face lands on the cup (x, z).

    Inside the footprint, clear of the castellated edges the board is soldered
    along.  These carry no fastener -- the Pro Micro has no holes at all, which
    is the whole reason it needs a cover rather than screws.
    """
    b = BOARDS["PROMICRO"]
    return [(b["x0"] + dx, b["z0"] + dz)
            for dx in (5.0, b["L"] - 5.0)
            for dz in (4.0, b["W"] - 4.0)]


def pm_cover_screws() -> list[tuple[float, float]]:
    """Where pm_cover bolts into the cup (x, z) — outside the board."""
    b = BOARDS["PROMICRO"]
    return [(b["x0"] + dx, b["z0"] + dz)
            for dx in (6.0, b["L"] - 6.0)
            for dz in (-4.0, b["W"] + 4.0)]


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
    # Qwiic glands so the I2C tails leave the sealed plenum.  There used to be
    # ONE, in the cup's top wall, which nothing could ever have used: the
    # BMP581's two Qwiic connectors are on its fore and aft edges, so a cable
    # leaving upward does not exist.  Two glands is twice the leak path of one
    # -- worth revisiting if the static readings look suspect -- but it is the
    # only arrangement the board's connectors allow without a jumper.
    bmp = BOARDS["BMP581"]
    bko = board_keepout(bmp)
    for c in bmp["conns"]:
        cz = bmp["z0"] + c["at"]
        gw = c["w"] + 1.5
        # At the BOARD plane.  The glands used to be cut at the cup's deep end,
        # y -11.1..-5.6, while the connectors sit at y -2.1..+2.0 -- so they
        # opened into a part of the plenum the BMP581 has nothing in, and no
        # cable could ever have passed through one.  Spanned from just behind
        # the connector body to just past the board's back face.
        gy0, gy1 = bko[2] - 0.5, board_y(bmp) + 0.5
        gx0 = (CUP_X0 - 1.0) if c["edge"] == "fore" else (BAY_X1 - 1.0)
        body = body.cut(_box(gx0, gy0, cz - gw / 2,
                             CUP_WALL + 2.0, gy1 - gy0, gw))

    # --- the ESP32 rides on the cup's back face ---------------------------
    # Four pads for it to sit on and four bosses for the cover to clamp into.
    # The bosses go on the Z sides: the board is as long as the cup, so there
    # is no room fore or aft, but ~7.7 mm above and below.
    pm = BOARDS["PROMICRO"]
    for px, pz in pm_support_pads():
        pad = _cyl_y(px, pz, CUP_Y0 - PM_STANDOFF, PM_STANDOFF, BOARD_POST_D / 2)
        body = _union_if_solid(body, pad)
    for bx, bz in pm_cover_screws():
        boss = _cyl_y(bx, bz, PM_COVER_Y, CUP_Y0 - PM_COVER_Y, BOSS_D / 2)
        body = _union_if_solid(body, boss)
        body = body.cut(_cyl_y(bx, bz, PM_COVER_Y - 1.0,
                               INS_DEPTH + 1.0, INS_HOLE_D / 2))
    return body


def build_pm_cover() -> cq.Workplane:
    """L7: the lid that CLAMPS the Pro Micro down (pm_cover.stl).

    This was a "tray" for three revisions and should never have been one.  A
    tray is a floor with a rim: the board lies in it, nothing holds it, and the
    rim could just as well have been printed into the plate -- which is exactly
    what the printed part turned out to be.  The Pro Micro has no mounting
    holes, so the only way to retain it is to trap it, and that needs something
    ABOVE it.

    The board now sits on four pads on the static bay's back face, bare back
    down.  This is a shallow lid that goes over its components and bears on the
    component-side border, bolting into four bosses on the cup.  Openings for
    the two things that must stay reachable: the USB-C on the aft end, and the
    Qwiic on the up edge.
    """
    b = BOARDS["PROMICRO"]
    ko = board_keepout(b)
    clr = 0.4
    # roof: outboard of the tallest component
    y_in = ko[2] - PM_COVER_CLR                    # inner face of the roof
    ox0, oz0 = b["x0"] - clr, b["z0"] - clr
    oL, oW = b["L"] + 2 * clr, b["W"] + 2 * clr

    body = _box(ox0 - 2.0, y_in - PM_COVER_T, oz0 - 2.0,
                oL + 4.0, PM_COVER_T, oW + 4.0)

    # Perimeter wall down onto the board's component-side border.  This is the
    # clamping surface: it bears on the PCB, not on the components.
    y_face = board_y(b) - b["pcb"]                 # component face
    wall_t = 2.0
    for dx, dz, sx, sz in ((-2.0, -2.0, oL + 4.0, wall_t),
                           (-2.0, oW + 2.0 - wall_t, oL + 4.0, wall_t),
                           (-2.0, -2.0, wall_t, oW + 4.0),
                           (oL + 2.0 - wall_t, -2.0, wall_t, oW + 4.0)):
        body = body.union(_box(ox0 + dx, y_in, oz0 + dz,
                               sx, y_face - y_in, sz))

    # USB-C must stay reachable for reflashing: notch the aft wall.
    usb = next(c for c in b["conns"] if c["name"] == "usb_c")
    body = body.cut(_box(ox0 + oL + 2.0 - wall_t - 1.0, y_in - PM_COVER_T - 1.0,
                         b["z0"] + usb["at"] - usb["w"] / 2 - 1.0,
                         wall_t + 2.0, PM_COVER_T + (y_face - y_in) + 2.0,
                         usb["w"] + 2.0))
    # Qwiic leaves the up edge.
    qw = next(c for c in b["conns"] if c["name"] == "qwiic")
    body = body.cut(_box(b["x0"] + qw["at"] - qw["w"] / 2 - 1.0,
                         y_in - PM_COVER_T - 1.0,
                         oz0 + oW + 2.0 - wall_t - 1.0,
                         qw["w"] + 2.0, PM_COVER_T + (y_face - y_in) + 2.0,
                         wall_t + 2.0))

    # Ears and clearance holes into the cup's bosses.
    for cx, cz in pm_cover_screws():
        body = body.union(_cyl_y(cx, cz, y_in - PM_COVER_T, PM_COVER_T, BOSS_D / 2))
        body = body.cut(_cyl_y(cx, cz, y_in - PM_COVER_T - 1.0,
                               PM_COVER_T + 2.0, LID_SCREW_D / 2))
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
            .transformed(offset=(bx, PITOT_AXIS_Y, PITOT_AXIS_Z))
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
    # OUTSIDE-DOWN, not flange-down.  Flange-down puts the cavity ceiling and
    # every inward-hanging feature — standoffs, bulkheads, rails — over open
    # air, and print-3 came out packed with support throughout the inside.
    # Landing the outer flank on the bed instead lets the whole interior grow
    # upward off the skin.  Measured on the real halves at a 45 deg threshold:
    #
    #     plate   support 11782 -> 8399 mm2 (-29%), bed contact 2096 -> 5288
    #     bowl    support 12337 -> 8666 mm2 (-30%), bed contact 2610 -> 6037
    #
    # The flank is flat over the whole midbody, so the first layer is a large
    # flat face rather than a thin flange ring.  Cost: the mating face is now
    # the TOP surface, and the aero skin takes the bed finish.
    ang = -90.0 if side > 0 else 90.0
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


def support_area(body: cq.Workplane, thresh_deg: float = 45.0) -> tuple[float, float]:
    """(area needing support, bed-contact area) for a body already in print pose.

    Turns "which way up should this print" from a matter of opinion into a
    number.  Counts downward-facing tessellated area steeper than the overhang
    threshold, and the flat area actually touching the bed.
    """
    verts, tris = body.val().tessellate(0.2, 0.3)
    zmin = min(v.z for v in verts)
    need = bed = 0.0
    lim = -math.cos(math.radians(90.0 - thresh_deg))
    for a, b, c in tris:
        pa, pb, pc = verts[a], verts[b], verts[c]
        ux, uy, uz = pb.x - pa.x, pb.y - pa.y, pb.z - pa.z
        vx, vy, vz = pc.x - pa.x, pc.y - pa.y, pc.z - pa.z
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        n = math.sqrt(nx * nx + ny * ny + nz * nz)
        if n < 1e-12:
            continue
        area, nz = 0.5 * n, nz / n
        if nz < -0.999 and (pa.z + pb.z + pc.z) / 3.0 - zmin < 0.15:
            bed += area
        elif nz < lim:
            need += area
    return need, bed


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


def model_stamp() -> str:
    """A short hash of this file, written into every exported STL's header.

    mtime cannot answer "was this built from the current model": restoring an
    old artifact with `git checkout` gives it a fresh mtime and stale content,
    which is exactly the case that matters.  A binary STL's 80-byte header is
    free space that slicers ignore, so the provenance travels with the file.
    """
    import hashlib
    src = Path(__file__).read_bytes()
    return f"kingfisher wing_pod_v3 {hashlib.sha256(src).hexdigest()[:16]}"


def _stamp_stl(path: str) -> None:
    head = model_stamp().encode()[:79].ljust(80, b"\0")
    with open(path, "r+b") as fh:
        fh.write(head)


def export_part(body: cq.Workplane, name: str, *, stl: bool = True) -> None:
    cq.exporters.export(body, f"{name}.step")
    if not stl:
        return
    cq.exporters.export(body, f"{name}.stl",
                        tolerance=0.03, angularTolerance=0.05)
    _stamp_stl(f"{name}.stl")
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
    cover = build_pm_cover()
    clamp = build_sun_clamp()

    export_part(for_print_half(right, 1), "pod_right")
    export_part(for_print_half(left, -1), "pod_left")
    for name, part, axis in (("tail_panel", panel, "x"),
                             ("static_bay", cup, "y"),
                             ("pm_cover", cover, "y"),
                             ("sun_clamp", clamp, "y")):
        flat = flat_for_print(part, axis)
        export_part(flat, name)
        check_print_bb(flat, name)
    lap("STL/STEP exported")

    for name, part, side in (("pod_right", right, 1), ("pod_left", left, -1)):
        dx, dy, dz = check_print_bb(for_print_half(part, side), name)
        flat = for_print_half(part, side)
        need, bed = support_area(flat)
        print(f"  P0 {name} print AABB {dx:.1f} x {dy:.1f} x {dz:.1f} mm "
              f"(bed {BED_LIMIT:.0f} x {BED_LIMIT:.0f} x {BED_Z:.0f}); "
              f"support {need:.0f} mm^2, bed contact {bed:.0f} mm^2")

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
        out.append(dict(name="flange", x=x, y=0.0, z=z, access="seam",
                        through_left=True))
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
    return [("sun_clamp", build_sun_clamp()),
            ("pm_cover", build_pm_cover()),
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
    rs = [max(r - inset, MIN_CORNER_R) for r in (r00, r10, r11, r01)]
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
