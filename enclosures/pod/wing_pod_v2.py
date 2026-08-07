#!/usr/bin/env python3
# =============================================================================
#  WING-MOUNTED AIR-DATA POD  v2  — left/right aerodynamic clamshell
#
#  Printed PETG halves (flat mating face on the bed, curved outer up) that mate
#  into a sealed aero shell.  Flat top deck mates a *separate* wing fairing
#  later; pod↔fairing latch is deferred.
#
#  Boards (Qwiic era — rearrangeable via BOARDS dict):
#    SparkFun Pro Micro ESP32-C3, Battery Babysitter (BQ27441), MMC5983MA,
#    BMP581, Qwiic 5V Boost, Holybro MS4525DO, LiPo ~50 x 6 x 70 mm.
#
#  Air data (pneumatically decoupled):
#    Prandtl total  -> MS4525 +
#    Prandtl static  -> MS4525 -
#    Pod multi-hole static bay -> BMP581 only
#    Optional 2nd static port in the pitot plug for a BMP581 tee test.
#
#  Pitot mount (SUN-adaptor replacement): tubes-in-tube
#    outer tube (ID ~10.2) full mount length; 3x inner tubes (OD 10 / ID 8)
#    with thick O-rings between segments sealing on the 8 mm Prandtl shaft;
#    printed aft plug (RTV) adapts 4 mm probe lines -> MS4525 barb tubing.
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
WALL = 2.5
FLAT_TOP_CHAMFER = 1.0  # soft break from flat top into side wall
BOTTOM_R = 14.0  # bottom rounding (keep < OUTER_W/2 - 1)
EDGE_FILLET = 1.2  # lengthwise outer edges where legal
FLANGE_W = 6.0  # mating-face flange width (into each half)
GASKET_W = 1.6
GASKET_D = 0.9
SHELL_SCREW_INSET = 3.0  # flange screw centres from outer skin

# Outer envelope (derived length prints below).  Tall blade for 70 mm battery.
# Width must fit battery pocket on centerline + BABY_W (33) on the +Y deck:
#   HALF_W >= BATT_POCKET_Y/2 + 1 + BABY_W + WALL  →  OUTER_W >= ~86
OUTER_W = 86.0  # Y, left-right (boards live in the +Y half)
OUTER_H = 78.0  # Z, bottom -> flat top (battery pocket 72 + walls)

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

# --- pitot tubes-in-tube (purchased metal + printed plug) -------------------
# Outer tube sits in the printed cradle (both halves).  Inner tubes + O-rings
# are loose parts; the script only models the cradle bore and the aft plug.
OUTER_TUBE_ID = 10.2
OUTER_TUBE_OD = 12.0  # VERIFY vs your tube stock
OUTER_TUBE_LEN = 100.0  # matches ~100 mm Prandtl mount section
INNER_TUBE_OD = 10.0
INNER_TUBE_ID = 8.0
INNER_TUBE_N = 3
ORING_T = 2.5  # axial thickness between inner segments (>~2)
ORING_ID = 7.0  # < 8 mm so it seals on the Prandtl shaft
CRADLE_CLEAR = 0.15  # print clearance on OUTER_TUBE_OD
PITOT_AXIS_Z = 28.0  # cradle axis height from outer bottom
NOSE_EXTENSION = 4.0  # solid nose ahead of tube mouth

# Aft plug (prints separately; RTV into outer tube)
PLUG_LEN = 14.0
PLUG_OD = OUTER_TUBE_ID - 0.25  # slip + RTV
PLUG_FLANGE_OD = OUTER_TUBE_OD + 2.0
PLUG_FLANGE_T = 2.5
# Probe-side tubing (4 mm fittings on the Prandtl)
PROBE_TUBE_OD = 4.0
PROBE_BORE = 4.2
# MS4525DO: datasheet 1/8" barb -> 3/32" ID tubing (~2.38 mm ID).
# v1 calipers: barb tip Ø2.1, shoulder Ø3.5.  Sensor-side bore holds the
# smaller OD line that slips over the barb tip.
MS_TUBE_OD = 3.5  # typical silicone OD over 3/32" ID line (VERIFY)
MS_BORE = 3.7
MS_BARB_TIP_D = 2.1
MS_BARB_SHOULDER_D = 3.5
MS_BARB_DY = 4.3  # barb centre spacing on Holybro carrier
# Plug port layout (local: +X toward probe / nose when installed)
PLUG_TOTAL_Y = 2.4
PLUG_STATIC_Y = -2.4
PLUG_STATIC2_Y = 0.0  # optional 2nd static (BMP581 tee test); Z offset
PLUG_STATIC2_Z = 2.8
PLUG_ENABLE_STATIC2 = True

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
BAY_WALL = 2.5
STATIC_HOLE_D = 1.6
STATIC_HOLE_ROWS = 2
STATIC_HOLE_COLS = 5
STATIC_HOLE_PITCH_X = 4.5
STATIC_HOLE_PITCH_Z = 4.5

# USB-C window (Pro Micro)
USBC_W, USBC_H = 12.0, 7.0

# Printer bed (diagonal OK)
BED = 220.0

# =============================================================================
# DERIVED LAYOUT  (x = 0 at outer nose tip, +X aft)
# =============================================================================
HALF_W = OUTER_W / 2
INNER_W = OUTER_W - 2 * WALL
INNER_H = OUTER_H - 2 * WALL
CRADLE_R = OUTER_TUBE_OD / 2 + CRADLE_CLEAR
# Packing note (not modeled): INNER_TUBE_N segments + (N-1) O-rings ≈ OUTER_TUBE_LEN.
CRADLE_LEN = OUTER_TUBE_LEN
PLUG_X0 = NOSE_EXTENSION + CRADLE_LEN  # aft face of outer tube / plug flange seat
NOSE_BULKHEAD_X = PLUG_X0 + PLUG_FLANGE_T + 1.0

# Electronics in the +Y bay.  MS/Boost sit *beside* the pitot cradle (under the
# tube in Z) to reclaim length; battery X-range is shared with the Babysitter;
# mag stays at the extreme aft, far from BQ27441 / LiPo.
DECK_X0 = NOSE_EXTENSION + 6.0
MS_X0 = DECK_X0
BOOST_X0 = MS_X0 + MS_L + 2.5
BATT_X0 = max(NOSE_BULKHEAD_X + 1.0, BOOST_X0 + BOOST_L + 2.5)
# Babysitter + Pro Micro share the battery's X-span on the +Y deck (battery
# is on the centerline).  Mag stays aft of the static bay.
BABY_X0 = BATT_X0
PM_X0 = BABY_X0 + BABY_L + 0.5
assert PM_X0 + PM_L <= BATT_X0 + BATT_POCKET_X + 0.6, (
    "Pro Micro does not fit in battery X-span; widen pack or shorten Baby gap"
)
BAY_X0 = max(PM_X0 + PM_L, BATT_X0 + BATT_POCKET_X) + 2.0
BMP_X0 = BAY_X0 + BAY_WALL + 3.0
BAY_X1 = BMP_X0 + BMP581_L + 5.0 + BAY_WALL
MAG_X0 = BAY_X1 + 1.5
AFT_MARGIN = 3.5
OUTER_L = MAG_X0 + MAG_L + AFT_MARGIN

BATT_Y0 = -BATT_POCKET_Y / 2
BATT_Z0 = (OUTER_H - BATT_POCKET_Z) / 2

# Electronics sit on a deck in the +Y half, clear of the battery slot
DECK_Y0 = BATT_POCKET_Y / 2 + 1.0
DECK_Y1 = HALF_W - WALL
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
        xl=MAG_L,
        yl=MAG_W,
        holes=[(INSET, MAG_W / 2)],
    ),
}

# Clamshell flange screws (world X, Z); left half gets inserts, right clearance
_flange_xs = []
x = NOSE_EXTENSION + 12.0
while x < OUTER_L - 10.0:
    _flange_xs.append(x)
    x += 28.0
FLANGE_SCREWS = []
for fx in _flange_xs:
    FLANGE_SCREWS.append((fx, OUTER_H - SHELL_SCREW_INSET))
    FLANGE_SCREWS.append((fx, SHELL_SCREW_INSET))

# Half printed mating-face down: bed footprint ≈ OUTER_L x OUTER_H.
# At 45° on a square bed, axis-aligned BB side is (L+H)/sqrt(2).
BED_BB = (OUTER_L + OUTER_H) / math.sqrt(2)
print(
    f"OUTER  L x W x H = {OUTER_L:.1f} x {OUTER_W:.1f} x {OUTER_H:.1f} mm"
)
print(
    f"half bed BB @45deg ~{BED_BB:.0f} mm (bed {BED:.0f}; "
    f"{'OK diagonal' if BED_BB <= BED + 1 else 'TOO LONG'})"
)
print(
    f"pitot cradle: OD={OUTER_TUBE_OD} ID={OUTER_TUBE_ID} L={CRADLE_LEN}; "
    f"inners {INNER_TUBE_N}x OD{INNER_TUBE_OD}/ID{INNER_TUBE_ID}, "
    f"O-ring T={ORING_T} ID={ORING_ID}"
)
print(
    f"MS4525 barb tubing: tip Ø{MS_BARB_TIP_D} / shoulder Ø{MS_BARB_SHOULDER_D}; "
    f"datasheet 3/32\" ID (~2.38). Plug {PROBE_TUBE_OD} mm -> ~{MS_TUBE_OD} mm OD line."
)
assert BED_BB <= BED + 2.0, f"half footprint {BED_BB:.1f} exceeds {BED} bed even diagonal"
assert PITOT_AXIS_Z - CRADLE_R > WALL + 1.0, "pitot cradle too low"
assert PITOT_AXIS_Z + CRADLE_R < OUTER_H - WALL - 1.0, "pitot cradle too high"
assert BATT_Z0 > WALL, "battery pocket intersects floor"
assert BATT_Z0 + BATT_POCKET_Z < OUTER_H - WALL, "battery pocket intersects top"
assert DECK_Y1 - DECK_Y0 >= max(b["yl"] for b in BOARDS.values()) + 1.0, (
    "electronics deck too narrow for boards — widen OUTER_W"
)


# =============================================================================
# HELPERS
# =============================================================================
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


def half_body_solid(side: int) -> cq.Workplane:
    """
    Right (side=+1) or left (side=-1) outer solid: flat top, rounded bottom,
    vertical seam at y=0.  Built as a full rounded blank then bisected.
    """
    r = min(BOTTOM_R, HALF_W - 0.5, OUTER_H / 2 - 1)
    blank = (
        cq.Workplane("XY")
        .box(OUTER_L, OUTER_W, OUTER_H, centered=(False, True, False))
    )
    # Round the two bottom long edges and the bottom nose/tail a bit via fillet
    try:
        blank = blank.edges("<Z").fillet(r)
    except Exception:
        pass
    try:
        blank = blank.edges("|Z and >Y").fillet(EDGE_FILLET)
        blank = blank.edges("|Z and <Y").fillet(EDGE_FILLET)
    except Exception:
        pass
    # Soften flat-top side break
    try:
        blank = blank.edges(">Z and |X").fillet(FLAT_TOP_CHAMFER)
    except Exception:
        pass

    # Keep one half (include a thin overlap past y=0 so the seam isn't open)
    keep = (
        cq.Workplane("XY")
        .transformed(offset=(-1, 0, -1))
        .box(OUTER_L + 2, HALF_W + 1, OUTER_H + 2, centered=(False, False, False))
    )
    if side < 0:
        keep = (
            cq.Workplane("XY")
            .transformed(offset=(-1, -(HALF_W + 1), -1))
            .box(OUTER_L + 2, HALF_W + 1, OUTER_H + 2, centered=(False, False, False))
        )
    return blank.intersect(keep)


def hollow_half(side: int) -> cq.Workplane:
    """Shell half with wall thickness, open at the mating face (y=0)."""
    solid = half_body_solid(side)
    # Interior pocket: leave WALL on outer skin, floor, top; open to seam
    # Pocket extends past y=0 so the mating face is fully open for the flange.
    if side > 0:
        pocket = (
            cq.Workplane("XY")
            .transformed(offset=(WALL, -1.0, WALL))
            .box(OUTER_L - 2 * WALL, HALF_W - WALL + 1.0, OUTER_H - 2 * WALL,
                 centered=(False, False, False))
        )
    else:
        pocket = (
            cq.Workplane("XY")
            .transformed(offset=(WALL, -(HALF_W - WALL), WALL))
            .box(OUTER_L - 2 * WALL, HALF_W - WALL + 1.0, OUTER_H - 2 * WALL,
                 centered=(False, False, False))
        )
    body = solid.cut(pocket)

    # Mating flange: thicken seam band to FLANGE_W
    if side > 0:
        flange = (
            cq.Workplane("XY")
            .transformed(offset=(WALL, 0, WALL))
            .box(OUTER_L - 2 * WALL, FLANGE_W, OUTER_H - 2 * WALL,
                 centered=(False, False, False))
        )
        # Hollow the flange interior so only a rim remains
        flange_cut = (
            cq.Workplane("XY")
            .transformed(offset=(WALL + 1.0, 0, WALL + 1.0))
            .box(OUTER_L - 2 * WALL - 2.0, FLANGE_W + 0.1, OUTER_H - 2 * WALL - 2.0,
                 centered=(False, False, False))
        )
        flange = flange.cut(flange_cut)
    else:
        flange = (
            cq.Workplane("XY")
            .transformed(offset=(WALL, -FLANGE_W, WALL))
            .box(OUTER_L - 2 * WALL, FLANGE_W, OUTER_H - 2 * WALL,
                 centered=(False, False, False))
        )
        flange_cut = (
            cq.Workplane("XY")
            .transformed(offset=(WALL + 1.0, -FLANGE_W - 0.1, WALL + 1.0))
            .box(OUTER_L - 2 * WALL - 2.0, FLANGE_W + 0.1, OUTER_H - 2 * WALL - 2.0,
                 centered=(False, False, False))
        )
        flange = flange.cut(flange_cut)
    body = body.union(flange)

    # Gasket groove on the right-half mating face only (O-cord / silicone).
    if side > 0:
        groove = (
            cq.Workplane("XY")
            .transformed(offset=(WALL + 3.0, -0.05, WALL + 3.0))
            .box(OUTER_L - 2 * WALL - 6.0, GASKET_D + 0.1, GASKET_W,
                 centered=(False, False, False))
        )
        groove2 = (
            cq.Workplane("XY")
            .transformed(
                offset=(WALL + 3.0, -0.05, OUTER_H - WALL - 3.0 - GASKET_W)
            )
            .box(OUTER_L - 2 * WALL - 6.0, GASKET_D + 0.1, GASKET_W,
                 centered=(False, False, False))
        )
        groove3 = (
            cq.Workplane("XY")
            .transformed(offset=(WALL + 3.0, -0.05, WALL + 3.0))
            .box(GASKET_W, GASKET_D + 0.1, OUTER_H - 2 * WALL - 6.0,
                 centered=(False, False, False))
        )
        groove4 = (
            cq.Workplane("XY")
            .transformed(
                offset=(OUTER_L - WALL - 3.0 - GASKET_W, -0.05, WALL + 3.0)
            )
            .box(GASKET_W, GASKET_D + 0.1, OUTER_H - 2 * WALL - 6.0,
                 centered=(False, False, False))
        )
        body = body.cut(groove).cut(groove2).cut(groove3).cut(groove4)

    return body


def add_pitot_cradle(body: cq.Workplane, side: int) -> cq.Workplane:
    """Semicylindrical clamp bore for OUTER_TUBE + nose mouth."""
    # Full cylinder cut — each half only contains its side, so this becomes a groove
    bore = x_cylinder(
        CRADLE_R,
        CRADLE_LEN + 1.0,
        NOSE_EXTENSION - 0.5,
        0.0,
        PITOT_AXIS_Z,
    )
    body = body.cut(bore)
    # Open the nose mouth through the front wall
    mouth = x_cylinder(
        CRADLE_R,
        NOSE_EXTENSION + 2.0,
        -1.0,
        0.0,
        PITOT_AXIS_Z,
    )
    body = body.cut(mouth)
    # Aft bulkhead bore for plug access / tube seating
    aft = x_cylinder(
        CRADLE_R + 0.2,
        8.0,
        PLUG_X0 - 1.0,
        0.0,
        PITOT_AXIS_Z,
    )
    body = body.cut(aft)

    # Reinforcing cradle bosses around the tube (both halves)
    for x in (NOSE_EXTENSION + 15.0, NOSE_EXTENSION + CRADLE_LEN - 15.0):
        rib = (
            cq.Workplane("YZ")
            .workplane(offset=x)
            .center(0, PITOT_AXIS_Z)
            .circle(CRADLE_R + 4.0)
            .circle(CRADLE_R + 0.05)
            .extrude(3.0)
        )
        # Keep only this half
        if side > 0:
            clip = (
                cq.Workplane("XY")
                .transformed(offset=(x - 1, 0, 0))
                .box(5, HALF_W, OUTER_H, centered=(False, False, False))
            )
        else:
            clip = (
                cq.Workplane("XY")
                .transformed(offset=(x - 1, -HALF_W, 0))
                .box(5, HALF_W, OUTER_H, centered=(False, False, False))
            )
        rib = rib.intersect(clip)
        body = body.union(rib)
    return body


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
    body = body.union(deck)

    for name, b in BOARDS.items():
        bx = b["x0"]
        # y is offset within the electronics bay
        by = DECK_Y0 + b["y0"]
        bz = b["z0"]
        if b["holes"]:
            for (hx, hy) in b["holes"]:
                post = insert_post(bx + hx, by + hy, bz, BOARD_POST_H, BOARD_POST_D)
                body = body.union(post)
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
                )
                body = body.union(pad)
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
                )
                body = body.union(nub)
    return body


def add_static_bay(body: cq.Workplane) -> cq.Workplane:
    """Sealed-ish BMP581 bay with multi-hole side wall (pod static only)."""
    bay_y0 = DECK_Y0
    bay_y1 = DECK_Y1
    bay_z0 = DECK_Z
    bay_z1 = min(OUTER_H - WALL - 1.0, DECK_Z + 28.0)

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
        )
        body = body.union(wall)

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


def build_plug() -> cq.Workplane:
    """
    Aft plug for the outer pitot tube.  Flange seats against the tube aft face;
    cylindrical body RTVs into OUTER_TUBE_ID.  Ports:
      - total:   PROBE_BORE -> MS_BORE (step)
      - static:  same
      - static2: optional BMP581 tee test (PROBE_BORE both ends)
    """
    body = (
        cq.Workplane("YZ")
        .circle(PLUG_FLANGE_OD / 2)
        .extrude(PLUG_FLANGE_T)
    )
    stem = (
        cq.Workplane("YZ")
        .workplane(offset=PLUG_FLANGE_T)
        .circle(PLUG_OD / 2)
        .extrude(PLUG_LEN)
    )
    body = body.union(stem)

    def port(y: float, z: float, probe_bore: float, ms_bore: float, dual_static: bool = False):
        # Through from flange face (-X side of plug local = sensor side) to stem tip
        total_len = PLUG_FLANGE_T + PLUG_LEN + 0.2
        # Sensor-side (flange face) bore for MS tubing
        sensor = (
            cq.Workplane("YZ")
            .workplane(offset=-0.1)
            .center(y, z)
            .circle(ms_bore / 2)
            .extrude(PLUG_FLANGE_T + 3.0)
        )
        # Probe-side bore for 4 mm tubing (from stem tip inward)
        probe = (
            cq.Workplane("YZ")
            .workplane(offset=PLUG_FLANGE_T + PLUG_LEN + 0.1)
            .center(y, z)
            .circle(probe_bore / 2)
            .extrude(-(PLUG_LEN + 1.0 if dual_static else PLUG_LEN - 1.0))
        )
        return sensor, probe

    s, p = port(PLUG_TOTAL_Y, 0.0, PROBE_BORE, MS_BORE)
    body = body.cut(s).cut(p)
    s, p = port(PLUG_STATIC_Y, 0.0, PROBE_BORE, MS_BORE)
    body = body.cut(s).cut(p)
    if PLUG_ENABLE_STATIC2:
        # Full 4 mm through for optional BMP581 feed test
        s, p = port(PLUG_STATIC2_Y, PLUG_STATIC2_Z, PROBE_BORE, PROBE_BORE, dual_static=True)
        body = body.cut(s).cut(p)
    return body


def for_print_half(half: cq.Workplane, side: int) -> cq.Workplane:
    """
    Mating face down on the bed, curved outer up.
    Right (+Y): rotate so +Y -> +Z.  Left (-Y): rotate so -Y -> +Z.
    """
    # Rotate ±90 deg about X: (x,y,z) -> (x, -z, y) for +90
    if side > 0:
        p = half.rotate((0, 0, 0), (1, 0, 0), -90)
    else:
        p = half.rotate((0, 0, 0), (1, 0, 0), 90)
    bb = p.val().BoundingBox()
    return p.translate((-bb.xmin, -bb.ymin, -bb.zmin))


# =============================================================================
if __name__ == "__main__":
    right = build_right()
    left = build_left()
    plug = build_plug()

    right_print = for_print_half(right, +1)
    left_print = for_print_half(left, -1)

    tol = 0.05
    cq.exporters.export(right_print, "pod_right.stl", tolerance=tol)
    cq.exporters.export(left_print, "pod_left.stl", tolerance=tol)
    cq.exporters.export(plug, "pitot_plug.stl", tolerance=tol)
    cq.exporters.export(right, "pod_right.step")
    cq.exporters.export(left, "pod_left.step")
    cq.exporters.export(plug, "pitot_plug.step")

    # Assembly STEP: left + right + plug at cradle aft (flange at PLUG_X0)
    asm = right.union(left)
    # plug local: flange at x=0, stem +X; YZ centres at 0 → shift to pitot axis
    plug_placed = build_plug().translate((PLUG_X0, 0.0, PITOT_AXIS_Z))
    try:
        asm = asm.union(plug_placed)
    except Exception as exc:
        print(f"assembly plug union skipped: {exc}")
    cq.exporters.export(asm, "pod_assembly.step")
    print("exported pod_left/right + pitot_plug STL/STEP + pod_assembly.step")
