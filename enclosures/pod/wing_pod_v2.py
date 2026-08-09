#!/usr/bin/env python3
# =============================================================================
#  WING-MOUNTED AIR-DATA POD  v2  — left/right aerodynamic clamshell
#
#  Printed PETG halves (flat mating face on the bed, curved outer up) that mate
#  into a sealed aero shell.  Midsection: flat top (wing fairing mate), flat
#  L/R sides, curved bottom.  Nose + tail are lofted fairings (not blunt
#  plates).  Electronics / board posts live on the RIGHT half only; left is
#  the cover.  Pod↔fairing latch is deferred.
#
#  Boards (Qwiic era — rearrangeable via BOARDS dict):
#    SparkFun Pro Micro ESP32-C3, Battery Babysitter (BQ27441), MMC5983MA,
#    BMP581, Qwiic 5V Boost, Holybro MS4525DO, LiPo ~50 x 6 x 70 mm.
#
#  Air data (pneumatically decoupled):
#    Prandtl total  -> SUN-B aft barb -> 6 mm hose -> COTS reducer -> MS4525 +
#    Prandtl static -> SUN-B middle barb -> same path -> MS4525 -
#    SUN-B forward (TE) barb capped / unused
#    Pod multi-hole static bay -> BMP581 only
#
#  Pitot mount: ESA SUN-B adapter (see SUN_B_CALIPERS.md).  Tip protrudes
#  one tip-length past the pod nose; Ø10.65 shoulder sits at x=0.  Printed
#  L/R cradle clamps the body, clears upward barbs, and locates the aft
#  blind recess on a boss.
#
#  Fasteners: M2.5 brass heat-set inserts (hub-case lessons: pilot = OD -
#  melt allowance, depth = insert length + extra, screw-relief bore below).
#
#  Printer: AnkerMake M5C (220 x 220) — body may print diagonally.
#  Re-run:  cd enclosures/pod && uv run --project .. python wing_pod_v2.py
# =============================================================================
from __future__ import annotations

import math

import cadquery as cq

# =============================================================================
# PARAMETERS (mm)
# =============================================================================

# --- shell / aero -----------------------------------------------------------
# Skinny rounded pod.  Construction cross-section is a tall ellipse, then
# chopped to a flat top (wing-fairing mate, square edge) and flat bottom
# (stands upright on the bench) with a radiused bottom edge for aero.
# Nose/tail are multi-station ogive lofts.  Section is *asymmetric* about the
# seam: thin left cover, wider right half for boards — total width ~52 mm.
WALL = 2.5
FLANGE_W = 6.0  # mating-face flange width (into each half)
GASKET_W = 1.6
GASKET_D = 0.9
SHELL_SCREW_INSET = 3.0  # flange screw centres from outer skin
NOSE_FAIR_LEN = 28.0  # tip -> full midsection
TAIL_FAIR_LEN = 20.0  # full midsection -> aft tip
OGIVE_STATIONS = 8  # loft stations in each fairing (smoothness)
# Construction ellipse overshoots the final flats so the chords have real width
# (cutting at the ellipse apex left a vanishing flat top).
TOP_CHOP = 10.0  # mm of ellipse above OUTER_H before the flat-top cut
BOTTOM_CHOP = 10.0  # mm of ellipse below z=0 before the flat-bottom cut
BOTTOM_EDGE_R = 8.0  # outer fillet on flat-bottom ↔ side (top stays square)

# Extents from the clamshell seam (y=0).  Right holds BABY_W=33 on the deck.
LEFT_EXTENT = 10.0   # -Y cover side (battery half + wall + margin)
RIGHT_EXTENT = 42.0  # +Y electronics side (fits BABY_W=33 on deck)
OUTER_W = LEFT_EXTENT + RIGHT_EXTENT  # ~52 mm overall
OUTER_H = 77.0  # Z, flat bottom -> flat top (battery pocket 72 + walls)
SECTION_YC = 0.5 * (RIGHT_EXTENT - LEFT_EXTENT)  # ellipse centre offset toward +Y
# Nose/tail tip centre in Y.  Ideal is the seam (0) so both halves get a tip;
# exact 0.0 breaks OCCT mid∪ogive fuse.  ~4 mm still reaches well into -Y.
TIP_YC = 4.0

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
LID_SCREW_D = 2.8  # clearance through the opposite half
LID_CB_D = 5.0
LID_CB_DEPTH = 2.2
BOSS_D = 7.0
BOARD_POST_D = 7.0
BOARD_POST_H = INS_DEPTH + SCREW_RELIEF_EXTRA + SCREW_RELIEF_FLOOR + 1.0

# --- ESA SUN-B pitot mount (calipers: SUN_B_CALIPERS.md, 2026-08-09) --------
# Pod nose tip x=0 is the tip→Ø10.65 shoulder.  Tip protrudes -X by SUN_TIP_LEN.
SUN_TIP_OD = 8.93
SUN_SMOOTH_OD = 10.65
SUN_THREAD_MAJOR = 11.76
SUN_BARREL_OD = 11.71
SUN_TIP_LEN = 24.75
SUN_SMOOTH_LEN = 27.50
SUN_THREAD_LEN = 25.37
SUN_TOTAL_LEN = 124.03
# Barb stations measured from SUN nose; subtract SUN_TIP_LEN for pod X.
SUN_BARB_TE_FROM_NOSE = 85.0
SUN_BARB_STATIC_FROM_NOSE = 100.0
SUN_BARB_PITOT_FROM_NOSE = 114.0
SUN_BARB_STEM_OD = 5.96
SUN_BARB_ABOVE_BARREL = 16.08  # barrel OD top → barb tip
SUN_RECESS_D = 6.03
SUN_RECESS_DEPTH = 7.06
CRADLE_CLEAR = 0.20  # print clearance on metal ODs
PITOT_AXIS_Z = 28.0  # cradle axis height from outer bottom
# MS4525DO: datasheet 1/8" barb -> 3/32" ID tubing (~2.38 mm ID).
# SUN barbs take 6 mm ID hose; step down with a COTS reducer (not printed).
MS_TUBE_OD = 3.5  # typical silicone OD over 3/32" ID line (VERIFY)
MS_BARB_TIP_D = 2.1
MS_BARB_SHOULDER_D = 3.5
MS_BARB_DY = 4.3  # barb centre spacing on Holybro carrier

# --- battery (slab on centerline) -------------------------------------------
# User intent: 50 mm X (fore-aft), ~6 mm Y (L-R), 70 mm Z (top-bottom).
# +1 mm each side for foam-tape wedge.
BATT_X = 50.0
BATT_Y = 6.0
BATT_Z = 70.0
BATT_CLR = 1.0
BATT_POCKET_X = BATT_X + 2 * BATT_CLR
BATT_POCKET_Y = BATT_Y + 2 * BATT_CLR
BATT_POCKET_Z = BATT_Z + 2 * BATT_CLR

# --- boards -----------------------------------------------------------------
STANDOFF_DECK_Z = 4.0  # interior floor thickness above outer bottom (right half)
PCB_T = 1.6
BMP581_L, BMP581_W = 25.4, 25.4
BOOST_L, BOOST_W = 24.5, 24.5
PM_L, PM_W = 17.8, 33.0  # long axis along Y
MAG_L, MAG_W = 19.0, 7.6
MS_L, MS_W = 22.9, 17.0
BABY_L, BABY_W = 33.0, 33.0
INSET = 2.54
MS_HOLE_INSET = 2.54
MS_HOLE_FLIP = False

# Multi-hole static bay (BMP581 only)
BAY_WALL = 2.0
STATIC_HOLE_D = 1.6
STATIC_HOLE_ROWS = 2
STATIC_HOLE_COLS = 5
STATIC_HOLE_PITCH_X = 4.5
STATIC_HOLE_PITCH_Z = 4.5

# USB-C window (Pro Micro)
USBC_W, USBC_H = 12.0, 7.0

# Printer bed — halves export already flange-down and rotated 45° for diagonal
BED = 220.0
BED_MARGIN = 10.0  # keep toolpaths inside (skirt/brim + slicer keepout)

# =============================================================================
# DERIVED LAYOUT  (x = 0 at outer nose tip, +X aft)
# =============================================================================
HALF_W = RIGHT_EXTENT  # used where "outer +Y skin" is needed (USB, static holes)
INNER_H = OUTER_H - 2 * WALL
# Stepped cradle radii (print clearance on each SUN OD).
CRADLE_R_TIP = SUN_TIP_OD / 2 + CRADLE_CLEAR
CRADLE_R_SMOOTH = SUN_SMOOTH_OD / 2 + CRADLE_CLEAR
CRADLE_R_THREAD = SUN_THREAD_MAJOR / 2 + CRADLE_CLEAR + 0.15
CRADLE_R_BARREL = SUN_BARREL_OD / 2 + CRADLE_CLEAR
CRADLE_R = max(CRADLE_R_THREAD, CRADLE_R_BARREL)  # largest clamp radius
SUN_BARB_TIP_ABOVE_AXIS = SUN_BARB_ABOVE_BARREL + SUN_BARREL_OD / 2
# World X: pod nose tip = SUN tip→smooth shoulder (tip protrudes -X).
SUN_SMOOTH_X0 = 0.0
SUN_THREAD_X0 = SUN_SMOOTH_LEN
SUN_THREAD_X1 = SUN_THREAD_X0 + SUN_THREAD_LEN
SUN_AFT_X = SUN_TOTAL_LEN - SUN_TIP_LEN
SUN_BARB_TE_X = SUN_BARB_TE_FROM_NOSE - SUN_TIP_LEN
SUN_BARB_STATIC_X = SUN_BARB_STATIC_FROM_NOSE - SUN_TIP_LEN
SUN_BARB_PITOT_X = SUN_BARB_PITOT_FROM_NOSE - SUN_TIP_LEN
SUN_RECESS_BOSS_D = SUN_RECESS_D - 0.30
SUN_RECESS_BOSS_LEN = SUN_RECESS_DEPTH - 0.80
SECTION_RY = 0.5 * OUTER_W  # ellipse semi-axis Y
# Construction ellipse is taller than OUTER_H so flat chops leave real chords.
SECTION_RZ = 0.5 * OUTER_H + max(TOP_CHOP, BOTTOM_CHOP)
SECTION_ZC = 0.5 * OUTER_H + 0.5 * (TOP_CHOP - BOTTOM_CHOP)
TIP_R = CRADLE_R + WALL + 1.5
# Flat chord half-width at the top/bottom cuts (for sanity prints / asserts).
_flat_t = (OUTER_H - SECTION_ZC) / SECTION_RZ
_flat_b = (0.0 - SECTION_ZC) / SECTION_RZ
FLAT_TOP_HALF_W = SECTION_RY * math.sqrt(max(0.0, 1.0 - _flat_t * _flat_t))
FLAT_BOT_HALF_W = SECTION_RY * math.sqrt(max(0.0, 1.0 - _flat_b * _flat_b))
# Aft bulkhead just behind SUN end-B recess boss.
NOSE_BULKHEAD_X = SUN_AFT_X + 3.0

# Electronics in the +Y bay on the RIGHT half only (left half is the cover).
# MS/Boost sit beside the pitot cradle; battery X-span shared with Babysitter
# + Pro Micro; mag aft of static bay but still in the constant midsection so
# the tail fairing can taper cleanly.
DECK_X0 = max(4.0, NOSE_FAIR_LEN * 0.35)
MS_X0 = DECK_X0
BOOST_X0 = MS_X0 + MS_L + 2.0
BATT_X0 = max(NOSE_BULKHEAD_X + 0.5, BOOST_X0 + BOOST_L + 2.0)
BABY_X0 = BATT_X0
PM_X0 = BABY_X0 + BABY_L + 0.4
assert PM_X0 + PM_L <= BATT_X0 + BATT_POCKET_X + 0.6, (
    "Pro Micro does not fit in battery X-span; widen pack or shorten Baby gap"
)
BAY_X0 = max(PM_X0 + PM_L, BATT_X0 + BATT_POCKET_X) + 1.0
BMP_X0 = BAY_X0 + BAY_WALL + 1.5
BAY_X1 = BMP_X0 + BMP581_L + 2.5 + BAY_WALL
MAG_X0 = BAY_X1 + 0.8
# Constant midsection ends after mag; tail fairing is empty taper aft of that.
MID_END_X = MAG_X0 + MAG_W + 1.0
OUTER_L = MID_END_X + TAIL_FAIR_LEN
# Flange / screws only where the section is constant (not in the fairings)
FLANGE_X0 = NOSE_FAIR_LEN + 2.0
FLANGE_X1 = MID_END_X - 2.0

BATT_Y0 = -BATT_POCKET_Y / 2
BATT_Z0 = (OUTER_H - BATT_POCKET_Z) / 2

# Electronics sit on a deck in the +Y half, clear of the battery slot
DECK_Y0 = BATT_POCKET_Y / 2 + 1.0
DECK_Y1 = RIGHT_EXTENT - WALL
DECK_Z = WALL + STANDOFF_DECK_Z

# Board Y placements (board-local origin = corner; world y = DECK_Y0 + y0)
MS_Y0 = 1.0
BOOST_Y0 = 1.0
BABY_Y0 = 0.5
PM_Y0 = 0.5
BMP_Y0 = 1.0
MAG_Y0 = 1.0

_MSH = [
    (MS_HOLE_INSET, MS_W - MS_HOLE_INSET),
    (MS_L - MS_HOLE_INSET, MS_HOLE_INSET),
]
if MS_HOLE_FLIP:
    _MSH = [
        (MS_HOLE_INSET, MS_HOLE_INSET),
        (MS_L - MS_HOLE_INSET, MS_W - MS_HOLE_INSET),
    ]

BOARDS = {
    "MS4525": dict(
        x0=MS_X0, y0=MS_Y0, z0=DECK_Z, xl=MS_L, yl=MS_W, holes=_MSH
    ),
    "BOOST": dict(
        x0=BOOST_X0,
        y0=BOOST_Y0,
        z0=DECK_Z,
        xl=BOOST_L,
        yl=BOOST_W,
        holes=[
            (INSET, INSET),
            (BOOST_L - INSET, INSET),
            (INSET, BOOST_W - INSET),
            (BOOST_L - INSET, BOOST_W - INSET),
        ],
    ),
    "BABY": dict(
        x0=BABY_X0,
        y0=BABY_Y0,
        z0=DECK_Z,
        xl=BABY_L,
        yl=BABY_W,
        holes=[
            (2.5, 2.5),
            (BABY_L - 2.5, 2.5),
            (2.5, BABY_W - 2.5),
            (BABY_L - 2.5, BABY_W - 2.5),
        ],
    ),
    "PROMICRO": dict(
        x0=PM_X0, y0=PM_Y0, z0=DECK_Z, xl=PM_L, yl=PM_W, holes=[]
    ),
    "BMP581": dict(
        x0=BMP_X0,
        y0=BMP_Y0,
        z0=DECK_Z,
        xl=BMP581_L,
        yl=BMP581_W,
        holes=[(INSET, INSET), (BMP581_L - INSET, INSET)],
    ),
    "MAG": dict(
        x0=MAG_X0,
        y0=MAG_Y0,
        z0=DECK_Z,
        xl=MAG_W,  # short axis along X (saves body length)
        yl=MAG_L,
        holes=[(MAG_W / 2, INSET)],  # hole opposite Qwiic; Qwiic faces +Y
    ),
}

# Clamshell flange screws (world X, Z); left half gets inserts, right clearance
_flange_xs = []
x = FLANGE_X0 + 8.0
while x < FLANGE_X1 - 8.0:
    _flange_xs.append(x)
    x += 28.0
FLANGE_SCREWS = []
for fx in _flange_xs:
    FLANGE_SCREWS.append((fx, OUTER_H - SHELL_SCREW_INSET))
    FLANGE_SCREWS.append((fx, SHELL_SCREW_INSET))

# Half printed flange-down then rotated 45°: AABB side ≈ (L+H)/sqrt(2).
BED_BB = (OUTER_L + OUTER_H) / math.sqrt(2)
BED_LIMIT = BED - BED_MARGIN
print(
    f"OUTER  L x W x H = {OUTER_L:.1f} x {OUTER_W:.1f} x {OUTER_H:.1f} mm"
)
print(
    f"flats: top/bottom chords ~{2 * FLAT_TOP_HALF_W:.1f} mm "
    f"(top square-edge; bottom R={BOTTOM_EDGE_R}); "
    f"mid≡ogive ellipse so junction has no freestream step"
)
assert BOTTOM_EDGE_R < 0.5 * OUTER_W - 2.0, "BOTTOM_EDGE_R too large for body width"
assert TOP_CHOP >= 4.0 and BOTTOM_CHOP >= 4.0, "chops too small for fairing flat chords"
print(
    f"half bed BB @45deg ~{BED_BB:.1f} mm (limit {BED_LIMIT:.0f} = "
    f"{BED:.0f}-{BED_MARGIN:.0f} margin; "
    f"{'OK' if BED_BB <= BED_LIMIT else 'TOO LONG'})"
)
print(
    f"SUN-B cradle: tipØ{SUN_TIP_OD} protrude {SUN_TIP_LEN}; "
    f"smoothØ{SUN_SMOOTH_OD}@{SUN_SMOOTH_X0:.1f}..{SUN_THREAD_X0:.1f}; "
    f"threadØ{SUN_THREAD_MAJOR}..{SUN_THREAD_X1:.1f}; "
    f"barbs TE/S/P @ {SUN_BARB_TE_X:.1f}/{SUN_BARB_STATIC_X:.1f}/{SUN_BARB_PITOT_X:.1f}; "
    f"aft @ {SUN_AFT_X:.1f}"
)
print(
    f"MS4525: tip Ø{MS_BARB_TIP_D} / shoulder Ø{MS_BARB_SHOULDER_D}; "
    f"3/32\" ID (~2.38). SUN 6 mm hose -> COTS reducer -> ~{MS_TUBE_OD} mm OD line."
)
assert BED_BB <= BED_LIMIT, (
    f"half footprint {BED_BB:.1f} exceeds bed limit {BED_LIMIT:.1f}; shorten OUTER_L"
)
assert PITOT_AXIS_Z - CRADLE_R > WALL + 1.0, "pitot cradle too low"
assert PITOT_AXIS_Z + SUN_BARB_TIP_ABOVE_AXIS + 6.0 < OUTER_H - WALL, (
    "SUN barb tips + hose need more headroom — raise OUTER_H or lower PITOT_AXIS_Z"
)
# Pod X of thread start equals smooth length; SUN-nose station T3 = tip+smooth.
assert abs(SUN_THREAD_X0 - SUN_SMOOTH_LEN) < 0.05
assert abs((SUN_TIP_LEN + SUN_SMOOTH_LEN) - (SUN_THREAD_X0 + SUN_TIP_LEN)) < 0.05
assert abs(SUN_AFT_X - (SUN_TOTAL_LEN - SUN_TIP_LEN)) < 0.05
assert BATT_Z0 >= WALL - 0.05, "battery pocket intersects floor"
assert BATT_Z0 + BATT_POCKET_Z <= OUTER_H - WALL + 0.05, "battery pocket intersects top"
assert DECK_Y1 - DECK_Y0 >= max(b["yl"] for b in BOARDS.values()) + 1.0, (
    "electronics deck too narrow for boards — widen OUTER_W"
)


# =============================================================================
# HELPERS
# =============================================================================
def _union_if_solid(body: cq.Workplane, part: cq.Workplane) -> cq.Workplane:
    """Union only when intersect/clip left a real solid (skip empty scraps)."""
    try:
        solids = part.val().Solids()
    except Exception:
        return body
    if not solids or sum(s.Volume() for s in solids) < 1e-3:
        return body
    return body.union(part)


def insert_post(x: float, y: float, z0: float, h: float, d: float = BOARD_POST_D) -> cq.Workplane:
    """Post with heat-set insert pilot + screw-relief bore from the top."""
    post = (
        cq.Workplane("XY")
        .transformed(offset=(x, y, z0))
        .circle(d / 2)
        .extrude(h)
    )
    post = (
        post.faces(">Z")
        .workplane()
        .center(0, 0)
        .hole(INS_HOLE_D, min(INS_DEPTH, h - SCREW_RELIEF_FLOOR))
    )
    relief = min(INS_DEPTH + SCREW_RELIEF_EXTRA, h - SCREW_RELIEF_FLOOR)
    if relief > INS_DEPTH + 0.05:
        post = post.faces(">Z").workplane().center(0, 0).hole(SCREW_RELIEF_D, relief)
    return post


def x_cylinder(r: float, length: float, x0: float, y: float, z: float) -> cq.Workplane:
    return (
        cq.Workplane("YZ")
        .workplane(offset=x0)
        .center(y, z)
        .circle(r)
        .extrude(length)
    )


def _ogive_scale(t: float) -> float:
    """Smooth 0→1 fairing scale (cosine)."""
    t = max(0.0, min(1.0, t))
    return 0.5 - 0.5 * math.cos(math.pi * t)


def _flat_caps(solid: cq.Workplane, inset: float) -> cq.Workplane:
    """Chop construction ellipse to flat top (square) + flat bottom."""
    z_top = OUTER_H - inset
    z_bot = inset
    top_cut = (
        cq.Workplane("XY")
        .box(OUTER_L + 40, OUTER_W + 40, 40, centered=(False, True, False))
        .translate((-20, SECTION_YC, z_top))
    )
    bot_cut = (
        cq.Workplane("XY")
        .box(OUTER_L + 40, OUTER_W + 40, 40, centered=(False, True, False))
        .translate((-20, SECTION_YC, z_bot - 40))
    )
    return solid.cut(top_cut).cut(bot_cut)


def _ellipse_mid(inset: float, x0: float, length: float) -> cq.Workplane:
    """
    Constant midsection = same construction ellipse the ogives end on.
    Must match the fairing end-station or the mid's square face sticks into
    the freestream (high-drag step at the nose/tail junction).
    """
    ry = max(SECTION_RY - inset, 3.0)
    rz = max(SECTION_RZ - inset, 3.0)
    return (
        cq.Workplane("YZ")
        .center(SECTION_YC, SECTION_ZC)
        .ellipse(ry, rz)
        .extrude(length)
        .translate((x0, 0, 0))
    )


def _bottom_chord_half_w(inset: float) -> float:
    """Half-width of the flat-bottom chord after _flat_caps at z=inset."""
    ry = max(SECTION_RY - inset, 1.0)
    rz = max(SECTION_RZ - inset, 1.0)
    factor = (inset - SECTION_ZC) / rz
    return ry * math.sqrt(max(0.0, 1.0 - factor * factor))


def _round_bottom_chord(solid: cq.Workplane, inset: float) -> cq.Workplane:
    """
    Radius flat-bottom ↔ ellipse edges along the midsection (top stays square).

    Applied after flat caps.  Uses (corner box − keep cylinder) at each bottom
    chord end — edge-fillet is unreliable on the lofted body.
    """
    r = BOTTOM_EDGE_R - inset
    if r < 0.5:
        return solid
    half = _bottom_chord_half_w(inset)
    if half <= r + 0.75:
        print(f"WARNING bottom R={r:.1f} skipped (chord half-width {half:.1f})")
        return solid
    z0 = inset
    x0 = NOSE_FAIR_LEN - 0.5
    length = (MID_END_X + 0.5) - x0
    for y_edge, inward in (
        (SECTION_YC - half, +1.0),
        (SECTION_YC + half, -1.0),
    ):
        y_ax = y_edge + inward * r
        z_ax = z0 + r
        keep = (
            cq.Workplane("YZ")
            .workplane(offset=x0)
            .center(y_ax, z_ax)
            .circle(r)
            .extrude(length)
        )
        # Outboard of the chord end × below z0+r — the sharp corner wedge.
        outboard = -inward
        y_lo = min(y_edge, y_edge + outboard * r) - 0.05
        y_hi = max(y_edge, y_edge + outboard * r) + 0.05
        corner = (
            cq.Workplane("XY")
            .transformed(offset=(x0 - 0.05, y_lo, z0 - 0.05))
            .box(length + 0.1, y_hi - y_lo, r + 0.1, centered=(False, False, False))
        )
        solid = solid.cut(corner.cut(keep))
    return solid


def _loft_ogive_nose(inset: float, tip_r: float) -> cq.Workplane:
    """
    Rounded nose: tip → full mid ellipse at NOSE_FAIR_LEN, then hold that
    ellipse a few mm into the midspan so the junction is a volume overlap
    (no freestream-facing mid butt face).
    """
    ry = max(SECTION_RY - inset, 3.0)
    rz = max(SECTION_RZ - inset, 3.0)
    zc_mid = SECTION_ZC
    tip_r = max(tip_r, 2.5)
    tip_yc = TIP_YC
    tip_zc = PITOT_AXIS_Z
    n = OGIVE_STATIONS
    x_full = NOSE_FAIR_LEN
    x_hold = NOSE_FAIR_LEN + 2.5  # into mid
    s = (
        cq.Workplane("YZ")
        .workplane(offset=inset)
        .center(tip_yc, tip_zc)
        .circle(tip_r)
    )
    prev_x, prev_yc, prev_zc = inset, tip_yc, tip_zc
    for i in range(1, n):
        t = i / (n - 1)
        sc = _ogive_scale(t)
        x = inset + (x_full - inset) * t
        yc = tip_yc + (SECTION_YC - tip_yc) * sc
        zc = tip_zc + (zc_mid - tip_zc) * sc
        s = (
            s.workplane(offset=x - prev_x)
            .center(yc - prev_yc, zc - prev_zc)
            .ellipse(
                max(tip_r + (ry - tip_r) * sc, 2.0),
                max(tip_r + (rz - tip_r) * sc, 2.0),
            )
        )
        prev_x, prev_yc, prev_zc = x, yc, zc
    # Constant full section through the mid overlap.
    s = (
        s.workplane(offset=x_hold - prev_x)
        .center(SECTION_YC - prev_yc, zc_mid - prev_zc)
        .ellipse(ry, rz)
    )
    return s.loft(ruled=False)


def _loft_ogive_tail(inset: float, tip_r: float) -> cq.Workplane:
    """
    Rounded tail: hold full mid ellipse from MID_END_X-2.5, then taper to tip.
    Hold avoids an aft-facing rectangular mid butt into the wake.
    """
    ry = max(SECTION_RY - inset, 3.0)
    rz = max(SECTION_RZ - inset, 3.0)
    zc_mid = SECTION_ZC
    tip_r = max(tip_r, 2.5)
    tip_yc = TIP_YC
    tip_zc = zc_mid
    x_hold = MID_END_X - 2.5
    x_tip = OUTER_L - inset
    n = OGIVE_STATIONS
    s = (
        cq.Workplane("YZ")
        .workplane(offset=x_hold)
        .center(SECTION_YC, zc_mid)
        .ellipse(ry, rz)
    )
    prev_x, prev_yc, prev_zc = x_hold, SECTION_YC, zc_mid
    # Station at MID_END_X (still full), then taper.
    s = (
        s.workplane(offset=MID_END_X - prev_x)
        .center(0, 0)
        .ellipse(ry, rz)
    )
    prev_x = MID_END_X
    for i in range(1, n):
        t = i / (n - 1)
        sc = _ogive_scale(t)
        x = MID_END_X + (x_tip - MID_END_X) * t
        yc = SECTION_YC + (tip_yc - SECTION_YC) * sc
        zc = zc_mid + (tip_zc - zc_mid) * sc
        if i == n - 1:
            s = (
                s.workplane(offset=x - prev_x)
                .center(yc - prev_yc, zc - prev_zc)
                .circle(tip_r)
            )
        else:
            s = (
                s.workplane(offset=x - prev_x)
                .center(yc - prev_yc, zc - prev_zc)
                .ellipse(
                    max(ry + (tip_r - ry) * sc, 2.0),
                    max(rz + (tip_r - rz) * sc, 2.0),
                )
            )
        prev_x, prev_yc, prev_zc = x, yc, zc
    return s.loft(ruled=False)


def full_body_solid(inset: float = 0.0) -> cq.Workplane:
    """
    Mid + ogive nose/tail share one construction ellipse, then flat-chopped
    together so the fairing junction has no rectangular step into the flow.
    Bottom chord gets an aero radius; top flat stays square for the fairing mate.
    """
    tip_r = max(TIP_R - 0.35 * inset, 2.5)
    # Mid begins at the fairing junction; ogives hold full section into the mid.
    body_x0 = NOSE_FAIR_LEN
    body_len = MID_END_X - body_x0
    mid = _ellipse_mid(inset, body_x0, body_len)
    nose = _loft_ogive_nose(inset, tip_r)
    tail = _loft_ogive_tail(inset, tip_r * 0.75)
    body = mid.union(nose).union(tail)
    body = _flat_caps(body, inset)
    body = _round_bottom_chord(body, inset)
    return body


def _keep_half(side: int) -> cq.Workplane:
    """Bisect at the seam.  Right = +Y (boards); left = -Y (cover)."""
    if side > 0:
        return (
            cq.Workplane("XY")
            .transformed(offset=(-1, 0, -1))
            .box(OUTER_L + 2, RIGHT_EXTENT + 2, OUTER_H + 2, centered=(False, False, False))
        )
    return (
        cq.Workplane("XY")
        .transformed(offset=(-1, -(LEFT_EXTENT + 2), -1))
        .box(OUTER_L + 2, LEFT_EXTENT + 2, OUTER_H + 2, centered=(False, False, False))
    )


def hollow_half(side: int) -> cq.Workplane:
    """Faired shell half, open at the mating face, with flange + gasket."""
    outer = full_body_solid(inset=0.0)
    inner = full_body_solid(inset=WALL)
    hollow = outer.cut(inner)
    body = hollow.intersect(_keep_half(side))

    # Mating flange only along the constant midsection (not in nose/tail tapers)
    fx0, fx1 = FLANGE_X0, FLANGE_X1
    flen = fx1 - fx0
    if side > 0:
        flange = (
            cq.Workplane("XY")
            .transformed(offset=(fx0, 0, WALL))
            .box(flen, FLANGE_W, OUTER_H - 2 * WALL, centered=(False, False, False))
        )
        flange_cut = (
            cq.Workplane("XY")
            .transformed(offset=(fx0 + 1.0, 0, WALL + 1.0))
            .box(flen - 2.0, FLANGE_W + 0.1, OUTER_H - 2 * WALL - 2.0,
                 centered=(False, False, False))
        )
        flange = flange.cut(flange_cut)
    else:
        flange = (
            cq.Workplane("XY")
            .transformed(offset=(fx0, -FLANGE_W, WALL))
            .box(flen, FLANGE_W, OUTER_H - 2 * WALL, centered=(False, False, False))
        )
        flange_cut = (
            cq.Workplane("XY")
            .transformed(offset=(fx0 + 1.0, -FLANGE_W - 0.1, WALL + 1.0))
            .box(flen - 2.0, FLANGE_W + 0.1, OUTER_H - 2 * WALL - 2.0,
                 centered=(False, False, False))
        )
        flange = flange.cut(flange_cut)
    # Clip flange to outer envelope so rectangular blanks don't poke out of
    # the faired nose/tail (looked like stray tabs at the fairing junctions).
    flange = flange.intersect(outer)
    body = body.union(flange)

    # Gasket groove on the right-half mating face only (O-cord / silicone).
    if side > 0:
        groove = (
            cq.Workplane("XY")
            .transformed(offset=(fx0 + 2.0, -0.05, WALL + 3.0))
            .box(flen - 4.0, GASKET_D + 0.1, GASKET_W, centered=(False, False, False))
        )
        groove2 = (
            cq.Workplane("XY")
            .transformed(
                offset=(fx0 + 2.0, -0.05, OUTER_H - WALL - 3.0 - GASKET_W)
            )
            .box(flen - 4.0, GASKET_D + 0.1, GASKET_W, centered=(False, False, False))
        )
        groove3 = (
            cq.Workplane("XY")
            .transformed(offset=(fx0 + 2.0, -0.05, WALL + 3.0))
            .box(GASKET_W, GASKET_D + 0.1, OUTER_H - 2 * WALL - 6.0,
                 centered=(False, False, False))
        )
        groove4 = (
            cq.Workplane("XY")
            .transformed(offset=(fx1 - 2.0 - GASKET_W, -0.05, WALL + 3.0))
            .box(GASKET_W, GASKET_D + 0.1, OUTER_H - 2 * WALL - 6.0,
                 centered=(False, False, False))
        )
        body = body.cut(groove).cut(groove2).cut(groove3).cut(groove4)

    return body


def add_pitot_cradle(body: cq.Workplane, side: int) -> cq.Workplane:
    """
    SUN-B clamp: tip mouth + stepped bore, upward barb bay, aft recess boss,
    and bulkheads.  Barbs point +Z (ESA water tip).
    """
    # Protruding tip clearance through the nose (outside + into shoulder).
    body = body.cut(
        x_cylinder(
            CRADLE_R_TIP,
            SUN_TIP_LEN + 2.0,
            -(SUN_TIP_LEN + 1.0),
            0.0,
            PITOT_AXIS_Z,
        )
    )
    # Ø10.65 smooth barrel from pod nose tip to thread.
    body = body.cut(
        x_cylinder(
            CRADLE_R_SMOOTH,
            SUN_SMOOTH_LEN + 0.4,
            SUN_SMOOTH_X0 - 0.2,
            0.0,
            PITOT_AXIS_Z,
        )
    )
    # Threaded band (slightly roomier).
    body = body.cut(
        x_cylinder(
            CRADLE_R_THREAD,
            SUN_THREAD_LEN + 0.6,
            SUN_THREAD_X0 - 0.3,
            0.0,
            PITOT_AXIS_Z,
        )
    )
    # Matte barb barrel to aft face.
    body = body.cut(
        x_cylinder(
            CRADLE_R_BARREL,
            (SUN_AFT_X - SUN_THREAD_X1) + 1.0,
            SUN_THREAD_X1 - 0.3,
            0.0,
            PITOT_AXIS_Z,
        )
    )

    # Barb + hose tunnel above the axis (both halves; stems straddle y=0).
    barb_y = SUN_BARB_STEM_OD / 2 + 1.5
    barb_z1 = PITOT_AXIS_Z + SUN_BARB_TIP_ABOVE_AXIS + 8.0
    barb_x0 = SUN_THREAD_X1 - 1.0
    barb_x1 = SUN_AFT_X - 1.0
    barb_bay = (
        cq.Workplane("XY")
        .transformed(offset=(barb_x0, -barb_y, PITOT_AXIS_Z + CRADLE_R_BARREL - 1.0))
        .box(
            barb_x1 - barb_x0,
            2.0 * barb_y,
            barb_z1 - (PITOT_AXIS_Z + CRADLE_R_BARREL - 1.0),
            centered=(False, False, False),
        )
    )
    body = body.cut(barb_bay)

    # Right-half hose escape toward MS4525 / deck (+Y).
    if side > 0:
        hose = (
            cq.Workplane("XY")
            .transformed(
                offset=(
                    SUN_BARB_TE_X - 6.0,
                    0.0,
                    PITOT_AXIS_Z + CRADLE_R_BARREL - 0.5,
                )
            )
            .box(
                (SUN_BARB_PITOT_X + 8.0) - (SUN_BARB_TE_X - 6.0),
                DECK_Y0 + 8.0,
                14.0,
                centered=(False, False, False),
            )
        )
        body = body.cut(hose)

    outer = full_body_solid(inset=0.0)

    def _bulkhead(x: float, bore_r: float) -> cq.Workplane:
        if side > 0:
            y_span = RIGHT_EXTENT - WALL - 0.4
            plate = (
                cq.Workplane("XY")
                .transformed(offset=(x, 0.0, WALL))
                .box(3.0, y_span, OUTER_H - 2 * WALL, centered=(False, False, False))
            )
        else:
            y_span = LEFT_EXTENT - WALL - 0.4
            plate = (
                cq.Workplane("XY")
                .transformed(offset=(x, -y_span, WALL))
                .box(
                    3.0,
                    max(y_span, 1.0),
                    OUTER_H - 2 * WALL,
                    centered=(False, False, False),
                )
            )
        plate = plate.intersect(outer)
        # Keep barb bay open on the upper bulkheads in the barb zone.
        if x >= SUN_THREAD_X1 - 5.0:
            plate = plate.cut(barb_bay)
        return plate

    # Clamp bulkheads: mid-smooth, mid-thread, just forward of aft face.
    for x, br in (
        (SUN_SMOOTH_X0 + SUN_SMOOTH_LEN * 0.55, CRADLE_R_SMOOTH),
        (SUN_THREAD_X0 + SUN_THREAD_LEN * 0.5, CRADLE_R_THREAD),
        (SUN_AFT_X - 8.0, CRADLE_R_BARREL),
    ):
        body = _union_if_solid(body, _bulkhead(x, br))
        body = body.cut(x_cylinder(br, 5.0, x - 1.0, 0.0, PITOT_AXIS_Z))

    # Aft bulkhead + locating boss into end-B blind recess (Ø6.03 × 7.06).
    aft_bh_x = SUN_AFT_X + 0.2
    body = _union_if_solid(body, _bulkhead(aft_bh_x, CRADLE_R_BARREL))
    boss = x_cylinder(
        SUN_RECESS_BOSS_D / 2,
        SUN_RECESS_BOSS_LEN + 1.5,
        SUN_AFT_X - SUN_RECESS_BOSS_LEN,
        0.0,
        PITOT_AXIS_Z,
    )
    # Boss only where this half has material near the seam.
    if side > 0:
        boss_keep = (
            cq.Workplane("XY")
            .transformed(offset=(SUN_AFT_X - SUN_RECESS_BOSS_LEN - 0.5, -0.05, WALL))
            .box(
                SUN_RECESS_BOSS_LEN + 3.0,
                max(CRADLE_R_BARREL + 2.0, 4.0),
                OUTER_H - 2 * WALL,
                centered=(False, False, False),
            )
        )
    else:
        boss_keep = (
            cq.Workplane("XY")
            .transformed(
                offset=(
                    SUN_AFT_X - SUN_RECESS_BOSS_LEN - 0.5,
                    -(CRADLE_R_BARREL + 2.0),
                    WALL,
                )
            )
            .box(
                SUN_RECESS_BOSS_LEN + 3.0,
                CRADLE_R_BARREL + 2.05,
                OUTER_H - 2 * WALL,
                centered=(False, False, False),
            )
        )
    body = _union_if_solid(body, boss.intersect(boss_keep).intersect(outer))
    return body


def build_sun_placeholder() -> cq.Workplane:
    """Simple stepped solid for assembly STEP (not printed)."""
    tip = x_cylinder(SUN_TIP_OD / 2, SUN_TIP_LEN, -SUN_TIP_LEN, 0.0, PITOT_AXIS_Z)
    smooth = x_cylinder(
        SUN_SMOOTH_OD / 2, SUN_SMOOTH_LEN, SUN_SMOOTH_X0, 0.0, PITOT_AXIS_Z
    )
    thread = x_cylinder(
        SUN_THREAD_MAJOR / 2, SUN_THREAD_LEN, SUN_THREAD_X0, 0.0, PITOT_AXIS_Z
    )
    barrel = x_cylinder(
        SUN_BARREL_OD / 2,
        SUN_AFT_X - SUN_THREAD_X1,
        SUN_THREAD_X1,
        0.0,
        PITOT_AXIS_Z,
    )
    return tip.union(smooth).union(thread).union(barrel)


def add_battery_pocket(body: cq.Workplane) -> cq.Workplane:
    pocket = (
        cq.Workplane("XY")
        .transformed(offset=(BATT_X0, BATT_Y0, BATT_Z0))
        .box(BATT_POCKET_X, BATT_POCKET_Y, BATT_POCKET_Z, centered=(False, False, False))
    )
    return body.cut(pocket)


def add_flange_fasteners(body: cq.Workplane, side: int) -> cq.Workplane:
    for (fx, fz) in FLANGE_SCREWS:
        if side < 0:
            # Inserts in left half, axis along +Y into the flange
            pilot = (
                cq.Workplane("XZ")
                .workplane(offset=-0.01)
                .center(fx, fz)
                .circle(INS_HOLE_D / 2)
                .extrude(-INS_DEPTH)
            )
            body = body.cut(pilot)
            relief_depth = min(INS_DEPTH + SCREW_RELIEF_EXTRA, FLANGE_W - SCREW_RELIEF_FLOOR)
            if relief_depth > INS_DEPTH:
                relief = (
                    cq.Workplane("XZ")
                    .workplane(offset=-0.01)
                    .center(fx, fz)
                    .circle(SCREW_RELIEF_D / 2)
                    .extrude(-relief_depth)
                )
                body = body.cut(relief)
        else:
            # Clearance + counterbore from the right outer side through flange
            hole = (
                cq.Workplane("XZ")
                .workplane(offset=FLANGE_W + 0.1)
                .center(fx, fz)
                .circle(LID_SCREW_D / 2)
                .extrude(-(FLANGE_W + 0.2))
            )
            body = body.cut(hole)
            cb = (
                cq.Workplane("XZ")
                .workplane(offset=FLANGE_W + 0.1)
                .center(fx, fz)
                .circle(LID_CB_D / 2)
                .extrude(-(LID_CB_DEPTH))
            )
            body = body.cut(cb)
    return body


def add_electronics_deck(body: cq.Workplane) -> cq.Workplane:
    """Right-half deck + board insert posts + locating nubs."""
    deck = (
        cq.Workplane("XY")
        .transformed(offset=(DECK_X0, DECK_Y0, WALL))
        .box(
            OUTER_L - WALL - DECK_X0,
            DECK_Y1 - DECK_Y0,
            STANDOFF_DECK_Z,
            centered=(False, False, False),
        )
    )
    # Deck blank is rectangular; clip to outer so it can't poke the curved skin.
    deck = deck.intersect(full_body_solid(inset=0.0))
    body = body.union(deck)

    outer_env = full_body_solid(inset=0.0)
    for name, b in BOARDS.items():
        bx = b["x0"]
        # y is offset within the electronics bay
        by = DECK_Y0 + b["y0"]
        bz = b["z0"]
        if b["holes"]:
            for (hx, hy) in b["holes"]:
                post = insert_post(bx + hx, by + hy, bz, BOARD_POST_H, BOARD_POST_D)
                # Posts near the +Y skin can poke through the ellipse — clip.
                body = _union_if_solid(body, post.intersect(outer_env))
        else:
            # Castellated Pro Micro: corner pads (clamp with foam later)
            for (px, py) in (
                (bx + 3, by + 3),
                (bx + b["xl"] - 3, by + 3),
                (bx + 3, by + b["yl"] - 3),
                (bx + b["xl"] - 3, by + b["yl"] - 3),
            ):
                pad = (
                    cq.Workplane("XY")
                    .transformed(offset=(px, py, bz))
                    .circle(2.2)
                    .extrude(3.0)
                    .intersect(outer_env)
                )
                body = _union_if_solid(body, pad)
        # Locating nubs
        for (nx, ny) in (
            (bx - 1.0, by - 1.0),
            (bx + b["xl"] + 1.0, by - 1.0),
            (bx - 1.0, by + b["yl"] + 1.0),
            (bx + b["xl"] + 1.0, by + b["yl"] + 1.0),
        ):
            if DECK_Y0 < ny < DECK_Y1 and DECK_X0 < nx < OUTER_L - WALL:
                nub = (
                    cq.Workplane("XY")
                    .transformed(offset=(nx, ny, bz))
                    .circle(1.3)
                    .extrude(BOARD_POST_H + PCB_T + 0.4)
                    .intersect(outer_env)
                )
                body = _union_if_solid(body, nub)
    return body


def add_static_bay(body: cq.Workplane) -> cq.Workplane:
    """Sealed-ish BMP581 bay with multi-hole side wall (pod static only)."""
    bay_y0 = DECK_Y0
    bay_y1 = DECK_Y1
    bay_z0 = DECK_Z
    bay_z1 = min(OUTER_H - WALL - 1.0, DECK_Z + 28.0)
    # Rectangular bay blanks poke the curved +Y skin near the bottom chord —
    # clip to outer before union (same lesson as flange / deck / posts).
    outer_env = full_body_solid(inset=0.0)

    # Three interior walls ( +X, -X, and a lid shelf); +Y uses outer skin
    for (x0, y0, sx, sy) in (
        (BAY_X0, bay_y0, BAY_WALL, bay_y1 - bay_y0),  # -X wall
        (BAY_X1 - BAY_WALL, bay_y0, BAY_WALL, bay_y1 - bay_y0),  # +X wall
        (BAY_X0, bay_y0, BAY_X1 - BAY_X0, BAY_WALL),  # -Y wall (toward battery)
    ):
        wall = (
            cq.Workplane("XY")
            .transformed(offset=(x0, y0, bay_z0))
            .box(sx, sy, bay_z1 - bay_z0, centered=(False, False, False))
            .intersect(outer_env)
        )
        body = _union_if_solid(body, wall)

    # Cable notch on -X wall
    notch = (
        cq.Workplane("XY")
        .transformed(
            offset=(BAY_X0 - 0.1, bay_y0 + (bay_y1 - bay_y0) / 2 - 2.5, bay_z1 - 4.0)
        )
        .box(BAY_WALL + 0.5, 5.0, 4.0, centered=(False, False, False))
    )
    body = body.cut(notch)

    # Multi-hole static array through +Y outer skin into the bay
    cx = (BAY_X0 + BAY_X1) / 2
    cz = (bay_z0 + bay_z1) / 2
    x0 = cx - (STATIC_HOLE_COLS - 1) * STATIC_HOLE_PITCH_X / 2
    z0 = cz - (STATIC_HOLE_ROWS - 1) * STATIC_HOLE_PITCH_Z / 2
    for i in range(STATIC_HOLE_COLS):
        for j in range(STATIC_HOLE_ROWS):
            hx = x0 + i * STATIC_HOLE_PITCH_X
            hz = z0 + j * STATIC_HOLE_PITCH_Z
            hole = (
                cq.Workplane("XZ")
                .workplane(offset=HALF_W + 0.1)
                .center(hx, hz)
                .circle(STATIC_HOLE_D / 2)
                .extrude(-(WALL + 3.0))
            )
            body = body.cut(hole)
    return body


def add_usb_cutout(body: cq.Workplane) -> cq.Workplane:
    pm = BOARDS["PROMICRO"]
    # USB-C on the board's -Y edge in board frame — face it toward +Y outer wall
    ux = pm["x0"] + pm["xl"] / 2
    uz = DECK_Z + 3.0 + PCB_T
    cut = (
        cq.Workplane("XZ")
        .workplane(offset=HALF_W + 0.1)
        .center(ux, uz)
        .rect(USBC_W, USBC_H, centered=True)
        .extrude(-(WALL + 4.0))
    )
    return body.cut(cut)


def build_right() -> cq.Workplane:
    body = hollow_half(+1)
    body = add_pitot_cradle(body, +1)
    body = add_battery_pocket(body)
    body = add_flange_fasteners(body, +1)
    body = add_electronics_deck(body)
    body = add_static_bay(body)
    body = add_usb_cutout(body)
    return body


def build_left() -> cq.Workplane:
    body = hollow_half(-1)
    body = add_pitot_cradle(body, -1)
    body = add_battery_pocket(body)
    body = add_flange_fasteners(body, -1)
    return body


def as_single_solid(wp: cq.Workplane, name: str = "part") -> cq.Workplane:
    """
    Collapse a Compound to one Solid for slicer-friendly STLs.
    AnkerMake Studio (and others) reject or silently fail on multi-body STLs.
    """
    solids = wp.val().Solids()
    if not solids:
        raise RuntimeError(f"{name}: no solids to export")
    if len(solids) == 1:
        return cq.Workplane(solids[0])

    out = solids[0]
    for s in solids[1:]:
        out = out.fuse(s)
    out = out.clean()
    fused = out.Solids()
    if len(fused) == 1:
        return cq.Workplane(fused[0])

    # Still disconnected — keep the main body, drop scraps (with a warning).
    fused = sorted(fused, key=lambda s: s.Volume(), reverse=True)
    main, scraps = fused[0], fused[1:]
    scrap_vol = sum(s.Volume() for s in scraps)
    print(
        f"WARNING {name}: dropping {len(scraps)} disconnected solid(s) "
        f"(total {scrap_vol:.1f} mm^3); fix unions if that is unexpected"
    )
    return cq.Workplane(main)


def for_print_half(half: cq.Workplane, side: int) -> cq.Workplane:
    """
    Print orientation:
      1) flange / mating face (y=0) on the bed, curved outer up
      2) rotate 45° about Z so the half lies on the bed diagonal
      3) translate to the first octant (min corner at origin)
    """
    # Right: +90° about X → (x,y,z)->(x,-z,y); y=0 → z=0, y>0 → z>0 (curve up).
    # Left:  -90° about X → (x,y,z)->(x, z,-y); y=0 → z=0, y<0 → z>0.
    # (Earlier exports had these signs swapped — flange ended up on top.)
    if side > 0:
        p = half.rotate((0, 0, 0), (1, 0, 0), 90)
    else:
        p = half.rotate((0, 0, 0), (1, 0, 0), -90)
    bb = p.val().BoundingBox()
    p = p.translate((-bb.xmin, -bb.ymin, -bb.zmin))
    p = p.rotate((0, 0, 0), (0, 0, 1), 45)
    bb = p.val().BoundingBox()
    p = p.translate((-bb.xmin, -bb.ymin, -bb.zmin))
    return p


def _print_bb_ok(name: str, solid: cq.Workplane, check_bed: bool = True) -> None:
    bb = solid.val().BoundingBox()
    print(
        f"{name} print BB: {bb.xlen:.1f} x {bb.ylen:.1f} x {bb.zlen:.1f} mm"
        + (f" (bed limit {BED_LIMIT:.0f})" if check_bed else "")
    )
    if check_bed:
        assert bb.xlen <= BED_LIMIT + 0.05, f"{name} X {bb.xlen:.1f} > bed limit"
        assert bb.ylen <= BED_LIMIT + 0.05, f"{name} Y {bb.ylen:.1f} > bed limit"
    assert bb.zmin > -0.05, f"{name} not sitting on bed (zmin={bb.zmin})"


def _stl_boundary_edges(path: str, ndigits: int = 5) -> tuple[int, int]:
    """Return (triangle_count, boundary_edge_count) for a binary STL."""
    import struct
    from collections import Counter

    data = open(path, "rb").read()
    n = struct.unpack_from("<I", data, 80)[0]
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


# =============================================================================
if __name__ == "__main__":
    right = as_single_solid(build_right(), "pod_right")
    left = as_single_solid(build_left(), "pod_left")

    right_print = for_print_half(right, +1)
    left_print = for_print_half(left, -1)

    assert len(right_print.val().Solids()) == 1, "pod_right print must be one solid"
    assert len(left_print.val().Solids()) == 1, "pod_left print must be one solid"

    _print_bb_ok("pod_right", right_print)
    _print_bb_ok("pod_left", left_print)

    # Tighter tessellation — looser settings left open edges on pod_left that
    # AnkerMake rejects as corrupt.  Assert watertight after write.
    tol, ang = 0.03, 0.05
    for name, solid in (
        ("pod_right.stl", right_print),
        ("pod_left.stl", left_print),
    ):
        cq.exporters.export(solid, name, tolerance=tol, angularTolerance=ang)
        n_tri, n_bound = _stl_boundary_edges(name)
        print(f"{name}: {n_tri} tris, {n_bound} boundary edges")
        assert n_bound == 0, f"{name} not watertight ({n_bound} boundary edges)"
        assert n_tri > 100, f"{name} suspiciously few triangles ({n_tri})"

    cq.exporters.export(right, "pod_right.step")
    cq.exporters.export(left, "pod_left.step")

    # Assembly STEP: halves + SUN-B placeholder (not printed).
    asm = right.union(left)
    try:
        asm = asm.union(build_sun_placeholder())
    except Exception as exc:
        print(f"assembly SUN placeholder union skipped: {exc}")
    cq.exporters.export(asm, "pod_assembly.step")
    print("exported pod_left/right STL/STEP + pod_assembly.step (with SUN placeholder)")

    # Full requirements gate (STL + geometry).  See REQUIREMENTS.md / validate_pod.py.
    from validate_pod import validate_all

    result = validate_all(stl_only=False)
    for line in result.notes:
        print(line)
    if not result.passed:
        raise SystemExit(
            f"validate_pod failed ({len(result.failures)} check(s)) — "
            "STLs are NOT print-ready"
        )
    print("validate_pod: all checks passed")
