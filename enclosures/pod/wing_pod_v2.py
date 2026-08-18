#!/usr/bin/env python3
# =============================================================================
#  WING-MOUNTED AIR-DATA POD  v2  — left/right aerodynamic clamshell
#
#  Printed PETG halves (flat mating face on the bed, curved outer up) that mate
#  into a sealed aero shell.  Midsection: flat top (wing fairing mate), flat
#  L/R sides, curved bottom.  Nose + tail are lofted fairings (not blunt
#  plates).  Electronics live on the RIGHT +Y wall (XZ boards, Y-axis inserts);
#  left is the cover.  Pod↔fairing latch is deferred.
#
#  Boards (Qwiic era — rearrangeable via BOARDS dict):
#    SparkFun Pro Micro ESP32-C3, Battery Babysitter (BQ27441), MMC5983MA,
#    BMP581, Qwiic 5V Boost, Holybro MS4525DO, LiPo ~50 x 6 x 70 mm.
#
#  Air data (pneumatically decoupled):
#    Prandtl total  -> SUN-B aft barb -> 6 mm hose -> COTS reducer -> MS4525 +
#    Prandtl static -> SUN-B middle barb -> same path -> MS4525 -
#    SUN-B forward (TE) barb capped / unused
#    Isolated static bay (BMP+mag) -> BMP581; serviceable cover after heat-set
#
#  Pitot mount: ESA SUN-B adapter (see SUN_B_CALIPERS.md).  Nose outer mold
#  line fairs into the tip OD with a ≥1.5 mm lip.  Ø10.65 shoulder seats on
#  the aft face of a tip-only nose bulkhead (x=SHOULDER_BH_T).  Aft blind
#  recess on a printed boss that is shorter than the cup (print-1: 2 mm extra
#  prevented the shoulder from seating).  Knurled band = integral L/R clamp
#  with FDM bore allowance; flange screws = press.
#  Stern is a blunt convex ellipsoidal loft (not a pin/nipple).
#
#  Fasteners: M2.5 brass heat-set inserts (hub-case lessons: pilot = OD -
#  melt allowance, depth = insert length + extra, screw-relief bore below).
#  Clamshell: screws through left cover → inserts in right electronics half.
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
FLANGE_W = 6.0  # mating-face flange depth into each half (±Y)
FLANGE_RAIL = 4.0  # perimeter rail on the open mating face (I4)
GASKET_W = 1.6
GASKET_D = 0.9
SHELL_SCREW_INSET = 3.0  # flange screw centres from outer skin (in the rails)
NOSE_FAIR_LEN = 28.0  # tip -> full midsection
# Slightly shorter than early v2 so SUN shoulder bulkhead (+SHOULDER_BH_T)
# still fits the 210 mm diagonal bed budget.
TAIL_FAIR_LEN = 12.0  # full midsection -> aft tip (was 17; trimmed for board X-span)
OGIVE_STATIONS = 8  # loft stations in each fairing (smoothness)
# Construction ellipse overshoots the final flats so the chords have real width
# (cutting at the ellipse apex left a vanishing flat top).
TOP_CHOP = 10.0  # mm of ellipse above OUTER_H before the flat-top cut
BOTTOM_CHOP = 10.0  # mm of ellipse below z=0 before the flat-bottom cut
# Bottom↔side radius: OCCT edge-fillet on the mid refuses to fuse cleanly with
# the ogives (open STL), and keep-cylinder booleans leave a freestream trench /
# rail.  Square corners until a section-native R is done properly.
BOTTOM_EDGE_R = 0.0

# Extents from the clamshell seam (y=0).  Right holds BABY_W=33 on the deck.
LEFT_EXTENT = 10.0   # -Y cover side (battery half + wall + margin)
RIGHT_EXTENT = 42.0  # +Y electronics side (fits BABY_W=33 on deck)
OUTER_W = LEFT_EXTENT + RIGHT_EXTENT  # ~52 mm overall
OUTER_H = 77.0  # Z, flat bottom -> flat top (battery pocket 72 + walls)
SECTION_YC = 0.5 * (RIGHT_EXTENT - LEFT_EXTENT)  # ellipse centre offset toward +Y
# Nose/tail tip centre in Y.  Ideal is the seam (0) so both halves get a tip;
# exact 0.0 breaks OCCT mid∪ogive fuse.  Nose stays near 0 (coaxial with
# the SUN tip); tail may sit a little into +Y.
NOSE_TIP_YC = 0.35
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
# Pod exterior nose ≈ x=0.  Ø10.65 shoulder seats at SHOULDER_BH_T.
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
# Print-1: boss ~2 mm too long — SUN could not reach aft BH / nose shoulder.
# Keep ≥2.5 mm unused cup depth (fillet at cup floor + FDM).
SUN_RECESS_BOSS_AXIAL_CLR = 2.50
SUN_RECESS_BOSS_DIA_CLR = 0.40  # was 0.25; easier entry into Ø6.03 cup
# Print-1 (PETG split half-pipes): 0.20 mm radial slip was OK on the aft
# barrel (~x=95) but tight on tip, mid-smooth (~x=20), and especially the
# knurled clamp (~x=30, was 0.08).  Per-station allowances; clamp stays
# tighter than slip so the knurl is still the grip.
CRADLE_CLEAR_TIP = 0.40
CRADLE_CLEAR_SMOOTH = 0.40
CRADLE_CLEAR_BARREL = 0.22
CLAMP_CLEAR = 0.28  # FDM; flange screws close the rest onto the knurl
# Nose bulkhead: tip-only bore; Ø10.65 shoulder seats on its aft face.
SHOULDER_BH_T = 3.5  # was 2.5; more meat at the mouth (left lip tore)
PITOT_AXIS_Z = 28.0  # cradle axis height from outer bottom
# MS4525DO: datasheet 1/8" barb -> 3/32" ID tubing (~2.38 mm ID).
# SUN barbs take 6 mm ID hose; step down with a COTS reducer (not printed).
MS_TUBE_OD = 3.5  # typical silicone OD over 3/32" ID line (VERIFY)
MS_BARB_TIP_D = 2.1
MS_BARB_SHOULDER_D = 3.5
MS_BARB_DY = 4.3  # barb centre spacing on Holybro carrier

# --- battery (slab on centerline) -------------------------------------------
# 50×6×70 mm pack (X×Y×Z) + 1 mm/side foam-tape.  Installed from the open
# mating face before close-up — pocket must not open to freestream (S1).
BATT_X = 50.0
BATT_Y = 6.0
BATT_Z = 70.0
BATT_CLR = 1.0
BATT_POCKET_X = BATT_X + 2 * BATT_CLR
BATT_POCKET_Y = BATT_Y + 2 * BATT_CLR
BATT_POCKET_Z = BATT_Z + 2 * BATT_CLR

# --- boards (wall-mount on +Y inner skin) -----------------------------------
# Floor posts failed print-1: iron cannot reach well bottoms, and outboard
# posts put PCBs through the elliptical wall.  Boards sit in XZ on *raised*
# standoffs (not flush on the land); inserts along Y, set from the open
# mating face.  Screw-relief continues past the insert into the land (hub).
PCB_T = 1.6
COMP_H = 8.0  # default component/Qwiic keepout inboard of the PCB
MS_COMP_H = 12.0  # MS4525 barbs + reducer
WALL_LAND_T = 7.0  # extra meat on +Y inner skin behind the standoffs
STANDOFF_H = 4.0  # raise PCB off the wall land (air gap; not flush)
STATIC_COVER_T = 2.2
STATIC_FRAME_T = INS_DEPTH + SCREW_RELIEF_EXTRA + SCREW_RELIEF_FLOOR  # ~9.5
STATIC_WINDOW_MARGIN = 2.0  # window vs board AABB (iron + PCB through)
STATIC_FRAME_LIP = 8.0  # ±Z frame around the window (cover screws live here)
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

# Exterior openings (only these may pierce the outer skin — see REQUIREMENTS S1):
#   • Prandtl / SUN tip mouth
#   • static-port hole array
#   • panel USB (CAB-15464) + rocker (COM-08837) + 2× 5 mm LED holders
# Pro Micro has no exterior USB window (program over Wi‑Fi / bench before close-up).

# Printer bed — halves export already flange-down and rotated 45° for diagonal
BED = 220.0
BED_MARGIN = 10.0  # keep toolpaths inside (skirt/brim + slicer keepout)

# =============================================================================
# DERIVED LAYOUT  (x = 0 at outer nose tip, +X aft)
# =============================================================================
HALF_W = RIGHT_EXTENT  # used where "outer +Y skin" is needed (USB, static holes)
INNER_H = OUTER_H - 2 * WALL
# Stepped cradle radii.  Knurled band uses CLAMP_CLEAR (integral split clamp).
CRADLE_R_TIP = SUN_TIP_OD / 2 + CRADLE_CLEAR_TIP
CRADLE_R_SMOOTH = SUN_SMOOTH_OD / 2 + CRADLE_CLEAR_SMOOTH
CRADLE_R_CLAMP = SUN_THREAD_MAJOR / 2 + CLAMP_CLEAR
CRADLE_R_BARREL = SUN_BARREL_OD / 2 + CRADLE_CLEAR_BARREL
CRADLE_R = max(CRADLE_R_CLAMP, CRADLE_R_BARREL)
SUN_BARB_TIP_ABOVE_AXIS = SUN_BARB_ABOVE_BARREL + SUN_BARREL_OD / 2
# World X: pod exterior nose ≈ 0.  SUN Ø10.65 shoulder seats on the aft face
# of the nose bulkhead at SHOULDER_BH_T (tip-only hole through that bulkhead).
SUN_SMOOTH_X0 = SHOULDER_BH_T  # shoulder / start of Ø10.65
SUN_TIP_X0 = SUN_SMOOTH_X0 - SUN_TIP_LEN  # free tip end (protrudes past x=0)
SUN_THREAD_X0 = SUN_SMOOTH_X0 + SUN_SMOOTH_LEN
SUN_THREAD_X1 = SUN_THREAD_X0 + SUN_THREAD_LEN
SUN_AFT_X = SUN_SMOOTH_X0 + (SUN_TOTAL_LEN - SUN_TIP_LEN)
SUN_BARB_TE_X = SUN_SMOOTH_X0 + (SUN_BARB_TE_FROM_NOSE - SUN_TIP_LEN)
SUN_BARB_STATIC_X = SUN_SMOOTH_X0 + (SUN_BARB_STATIC_FROM_NOSE - SUN_TIP_LEN)
SUN_BARB_PITOT_X = SUN_SMOOTH_X0 + (SUN_BARB_PITOT_FROM_NOSE - SUN_TIP_LEN)
SUN_RECESS_BOSS_D = SUN_RECESS_D - SUN_RECESS_BOSS_DIA_CLR
SUN_RECESS_BOSS_LEN = SUN_RECESS_DEPTH - SUN_RECESS_BOSS_AXIAL_CLR
# Integral clamp land covers most of the knurled band (forward of barbs).
CLAMP_X0 = SUN_THREAD_X0 + 0.5
CLAMP_LEN = SUN_THREAD_LEN - 1.5
CLAMP_R_OUTER = CRADLE_R_CLAMP + 5.0  # thick meat around the snug bore
SECTION_RY = 0.5 * OUTER_W  # ellipse semi-axis Y
# Construction ellipse is taller than OUTER_H so flat chops leave real chords.
SECTION_RZ = 0.5 * OUTER_H + max(TOP_CHOP, BOTTOM_CHOP)
SECTION_ZC = 0.5 * OUTER_H + 0.5 * (TOP_CHOP - BOTTOM_CHOP)
# Nose mouth: outer mold line fairs into the SUN tip OD.  Print-1 0.7 mm lip
# tore on the left half — keep ≥1.5 mm radial PETG at the entry.
NOSE_LIP_WALL = 1.60
NOSE_MOUTH_R = CRADLE_R_TIP + NOSE_LIP_WALL  # outer radius at x≈0
# Tail tip radius used only as a fallback scale; stern is hemispherical.
TIP_R = CRADLE_R + WALL + 1.5
# Flat chord half-width at the top/bottom cuts (for sanity prints / asserts).
_flat_t = (OUTER_H - SECTION_ZC) / SECTION_RZ
_flat_b = (0.0 - SECTION_ZC) / SECTION_RZ
FLAT_TOP_HALF_W = SECTION_RY * math.sqrt(max(0.0, 1.0 - _flat_t * _flat_t))
FLAT_BOT_HALF_W = SECTION_RY * math.sqrt(max(0.0, 1.0 - _flat_b * _flat_b))
# Aft bulkhead just behind SUN end-B recess boss.
NOSE_BULKHEAD_X = SUN_AFT_X + 3.0

# Electronics on the RIGHT +Y wall only (left half is the cover).
# Cradle bulkheads stay near the pitot bore; boards hang from the wall land.
BOARD_GAP = 5.0
CRADLE_LAND_Y = CRADLE_R_CLAMP + 7.0  # cradle plates stay near the pitot bore
# Inboard face of the +Y wall land.  Standoffs protrude toward the seam by
# STANDOFF_H so the PCB is raised off the wall; insert + screw-relief live in
# standoff + land (hub-case scheme: long screws pass through the insert).
Y_LAND = 32.0
Y_PCB = Y_LAND - STANDOFF_H
assert Y_PCB - PCB_T - MS_COMP_H >= CRADLE_LAND_Y + 1.0, (
    "MS keepout hits cradle — raise Y_LAND or shorten standoffs"
)


def ellipse_y_plus(z: float, inset: float = 0.0) -> float:
    """Approximate +Y skin of the construction ellipse at height z."""
    ry = max(SECTION_RY - inset, 1.0)
    rz = max(SECTION_RZ - inset, 1.0)
    t = (z - SECTION_ZC) / rz
    if abs(t) >= 0.999:
        return SECTION_YC
    return SECTION_YC + ry * math.sqrt(max(0.0, 1.0 - t * t))


# Wall-mounted XZ boards.  MS/Boost may overlap the clamp in X (they sit at
# y=Y_PCB, clamp land is y<CRADLE_LAND_Y).  Place MS under the barb stations
# so 6 mm hose stays short.
MS_X0 = SUN_BARB_TE_X - 6.0
MS_Z0 = 20.0
BOOST_X0 = MS_X0 + MS_L + BOARD_GAP
BOOST_Z0 = 20.0
_AFT_BH_X1 = SUN_AFT_X + 0.2 + 3.0
BATT_X0 = max(
    NOSE_BULKHEAD_X + BOARD_GAP,
    _AFT_BH_X1 + BOARD_GAP,
    BOOST_X0 + BOOST_L + BOARD_GAP,
)
BABY_X0 = BATT_X0
BABY_Z0 = 22.0
PM_X0 = BABY_X0 + BABY_L + 1.0
PM_Z0 = BABY_Z0  # 33 mm along Z (was along Y on the floor)
assert PM_X0 + PM_L <= BATT_X0 + BATT_POCKET_X + 0.6, (
    "Pro Micro does not fit in battery X-span; widen pack or shorten Baby gap"
)
BAY_X0 = max(PM_X0 + PM_L, BATT_X0 + BATT_POCKET_X) + BOARD_GAP
BMP_X0 = BAY_X0 + 1.5
BMP_Z0 = 22.0
BAY_X1 = BMP_X0 + BMP581_L + 4.0
MAG_X0 = BMP_X0
MAG_Z0 = BMP_Z0 + BMP581_W + BOARD_GAP
# Constant midsection ends after static holes; tail fairing is empty taper.
MID_END_X = BAY_X1 + 1.0
OUTER_L = MID_END_X + TAIL_FAIR_LEN
# Flange / screws only where the section is constant (not in the fairings)
FLANGE_X0 = NOSE_FAIR_LEN + 2.0
FLANGE_X1 = MID_END_X - 2.0

BATT_Y0 = -BATT_POCKET_Y / 2
BATT_Z0 = (OUTER_H - BATT_POCKET_Z) / 2

_MSH = [
    (MS_HOLE_INSET, MS_W - MS_HOLE_INSET),
    (MS_L - MS_HOLE_INSET, MS_HOLE_INSET),
]
if MS_HOLE_FLIP:
    _MSH = [
        (MS_HOLE_INSET, MS_HOLE_INSET),
        (MS_L - MS_HOLE_INSET, MS_W - MS_HOLE_INSET),
    ]

# Board local: xl along X, zl along Z, holes (hx, hz).  y is the land face.
BOARDS = {
    "MS4525": dict(
        x0=MS_X0, z0=MS_Z0, xl=MS_L, zl=MS_W, holes=_MSH, comp_h=MS_COMP_H
    ),
    "BOOST": dict(
        x0=BOOST_X0,
        z0=BOOST_Z0,
        xl=BOOST_L,
        zl=BOOST_W,
        holes=[
            (INSET, INSET),
            (BOOST_L - INSET, INSET),
            (INSET, BOOST_W - INSET),
            (BOOST_L - INSET, BOOST_W - INSET),
        ],
        comp_h=COMP_H,
    ),
    "BABY": dict(
        x0=BABY_X0,
        z0=BABY_Z0,
        xl=BABY_L,
        zl=BABY_W,
        holes=[
            (2.5, 2.5),
            (BABY_L - 2.5, 2.5),
            (2.5, BABY_W - 2.5),
            (BABY_L - 2.5, BABY_W - 2.5),
        ],
        comp_h=COMP_H,
    ),
    "PROMICRO": dict(
        x0=PM_X0,
        z0=PM_Z0,
        xl=PM_L,
        zl=PM_W,
        holes=[],  # castellated, no OEM holes — printed tray (L7)
        clamp_posts=[
            (3.0, -2.5),
            (PM_L - 3.0, -2.5),
            (3.0, PM_W + 2.5),
            (PM_L - 3.0, PM_W + 2.5),
        ],
        comp_h=COMP_H,
    ),
    "BMP581": dict(
        x0=BMP_X0,
        z0=BMP_Z0,
        xl=BMP581_L,
        zl=BMP581_W,
        holes=[
            (INSET, INSET),
            (BMP581_L - INSET, INSET),
            (INSET, BMP581_W - INSET),
            (BMP581_L - INSET, BMP581_W - INSET),
        ],
        comp_h=COMP_H,
    ),
    "MAG": dict(
        x0=MAG_X0,
        z0=MAG_Z0,
        xl=MAG_L,
        zl=MAG_W,
        holes=[(INSET, MAG_W / 2), (MAG_L - INSET, MAG_W / 2)],
        comp_h=6.0,
    ),
}


def board_standoffs(b: dict) -> list[tuple[float, float]]:
    """Local (hx, hz) for M2.5 insert standoffs: through-PCB holes or clamp posts."""
    posts = list(b.get("holes") or [])
    if not posts:
        posts = list(b.get("clamp_posts") or [])
    return posts


def board_keepout(b: dict) -> tuple[float, float, float, float, float, float]:
    """AABB of PCB + inboard components: (x0,x1,y0,y1,z0,z1)."""
    x0, x1 = b["x0"], b["x0"] + b["xl"]
    z0, z1 = b["z0"], b["z0"] + b["zl"]
    y1 = Y_PCB  # inboard face of raised standoffs (not the wall land)
    y0 = Y_PCB - PCB_T - b.get("comp_h", COMP_H)
    return x0, x1, y0, y1, z0, z1


def build_routes() -> list[dict]:
    """Named hose / Qwiic / USB / battery leads for QC + I6/L5 checks."""
    barb_z = PITOT_AXIS_Z + CRADLE_R_BARREL + 8.0
    y_run = CRADLE_LAND_Y + 2.5  # between cradle and wall keepouts
    ms = BOARDS["MS4525"]
    ms_x = ms["x0"] + 0.5 * ms["xl"]
    ms_z = ms["z0"] + ms["zl"]  # barbs on +Z edge
    ms_y = Y_PCB - ms["comp_h"]
    baby = BOARDS["BABY"]
    bmp = BOARDS["BMP581"]
    mag = BOARDS["MAG"]
    pm = BOARDS["PROMICRO"]
    boost = BOARDS["BOOST"]
    qy = Y_PCB - 4.0
    _, _, bay_y0, _, _, _ = _static_bay_box()
    return [
        dict(
            name="pitot_hose",
            kind="hose",
            pts=[
                (SUN_BARB_PITOT_X, 0.0, barb_z),
                (SUN_BARB_PITOT_X, y_run, barb_z),
                (ms_x + 2.0, y_run, barb_z),
                (ms_x + 2.0, y_run, ms_z + 4.0),
                (ms_x + 2.0, ms_y, ms_z + 2.0),
            ],
        ),
        dict(
            name="static_hose",
            kind="hose",
            pts=[
                (SUN_BARB_STATIC_X, 0.0, barb_z),
                (SUN_BARB_STATIC_X, y_run, barb_z),
                (ms_x - 2.0, y_run, barb_z),
                (ms_x - 2.0, y_run, ms_z + 4.0),
                (ms_x - 2.0, ms_y, ms_z + 2.0),
            ],
        ),
        dict(
            name="qwiic",
            kind="wire",
            pts=[
                (ms["x0"] + ms["xl"], qy, ms["z0"]),
                (ms["x0"] + ms["xl"], qy, boost["z0"]),
                (boost["x0"] + boost["xl"], qy, boost["z0"]),
                (baby["x0"], qy, baby["z0"]),
                (baby["x0"] + baby["xl"], qy, baby["z0"]),
                (pm["x0"], qy, pm["z0"]),
                (pm["x0"] + pm["xl"], qy, pm["z0"]),
                (bmp["x0"] - 2.0, qy, bmp["z0"]),
                (bmp["x0"] - 2.0, bay_y0 - 1.0, bmp["z0"] + 2.0),
                (bmp["x0"] + 4.0, bay_y0 - 1.0, bmp["z0"] + 2.0),
                (bmp["x0"] + 4.0, qy, bmp["z0"] + 2.0),
                (bmp["x0"] + 0.5 * bmp["xl"], qy, bmp["z0"]),
                (mag["x0"] + 0.5 * mag["xl"], qy, mag["z0"]),
            ],
        ),
        dict(
            name="usb_pigtail",
            kind="wire",
            pts=[
                (USB_X, PANEL_Y - PANEL_T, USB_Z),
                (USB_X, Y_PCB - 2.0, USB_Z),
                (baby["x0"] + 0.5 * baby["xl"], Y_PCB - baby["comp_h"], baby["z0"] + baby["zl"] - 4.0),
            ],
        ),
        dict(
            name="switch_leads",
            kind="wire",
            pts=[
                (SW_X, PANEL_Y - PANEL_T, SW_Z),
                (SW_X, Y_PCB - 2.0, SW_Z),
                (baby["x0"] + 4.0, Y_PCB - baby["comp_h"], baby["z0"] + baby["zl"] - 6.0),
            ],
        ),
        dict(
            name="led_leads",
            kind="wire",
            pts=[
                (LED_PWR_X, PANEL_Y - PANEL_T, LED_Z),
                (LED_CHG_X, PANEL_Y - PANEL_T, LED_Z),
                (USB_X, Y_PCB - 2.0, USB_Z),
            ],
        ),
        dict(
            name="batt_leads",
            kind="wire",
            pts=[
                (BATT_X0 + 8.0, BATT_Y0 + BATT_POCKET_Y, BATT_Z0 + 8.0),
                (baby["x0"] + 4.0, y_run, baby["z0"] + 4.0),
                (baby["x0"] + 4.0, Y_PCB - baby["comp_h"], baby["z0"] + 4.0),
            ],
        ),
    ]


# Clamshell flange screws (world X, Z): through left cover → heat-set inserts
# in the right (electronics) half.  Top + bottom row at each station.
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
    f"(square edges; bottom R deferred); "
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
    f"SUN-B cradle: tipØ{SUN_TIP_OD} @ {SUN_TIP_X0:.1f}..{SUN_SMOOTH_X0:.1f} "
    f"(protrude {-SUN_TIP_X0:.1f} past nose); "
    f"shoulder seat @ {SUN_SMOOTH_X0:.1f}; "
    f"smoothØ{SUN_SMOOTH_OD}+{CRADLE_CLEAR_SMOOTH}..{SUN_THREAD_X0:.1f}; "
    f"clampØ{SUN_THREAD_MAJOR}+{CLAMP_CLEAR} @{CLAMP_X0:.1f}..{CLAMP_X0+CLAMP_LEN:.1f}; "
    f"barbs TE/S/P @ {SUN_BARB_TE_X:.1f}/{SUN_BARB_STATIC_X:.1f}/{SUN_BARB_PITOT_X:.1f}; "
    f"aft boss len {SUN_RECESS_BOSS_LEN:.1f} (cup {SUN_RECESS_DEPTH:.1f})"
)
print(
    f"MS4525: tip Ø{MS_BARB_TIP_D} / shoulder Ø{MS_BARB_SHOULDER_D}; "
    f"3/32\" ID (~2.38). SUN 6 mm hose -> COTS reducer -> ~{MS_TUBE_OD} mm OD line."
)
print(
    f"wall land Y_LAND={Y_LAND:.1f}; standoff {STANDOFF_H:.1f} → Y_PCB={Y_PCB:.1f}; "
    f"insert {INS_DEPTH:.2f} + relief extra {SCREW_RELIEF_EXTRA:.1f} "
    f"(floor {SCREW_RELIEF_FLOOR:.1f})"
)
assert STANDOFF_H >= 4.0, "standoffs must raise PCBs off the wall (I8 underside clearance)"
assert STANDOFF_H + WALL_LAND_T + 0.2 >= INS_DEPTH + SCREW_RELIEF_EXTRA + SCREW_RELIEF_FLOOR, (
    "standoff+land too thin for hub-case insert + screw relief"
)
assert BED_BB <= BED_LIMIT, (
    f"half footprint {BED_BB:.1f} exceeds bed limit {BED_LIMIT:.1f}; shorten OUTER_L"
)
assert PITOT_AXIS_Z - CRADLE_R > WALL + 1.0, "pitot cradle too low"
assert PITOT_AXIS_Z + SUN_BARB_TIP_ABOVE_AXIS + 6.0 < OUTER_H - WALL, (
    "SUN barb tips + hose need more headroom — raise OUTER_H or lower PITOT_AXIS_Z"
)
assert abs(SUN_SMOOTH_X0 - SHOULDER_BH_T) < 0.05
assert abs(SUN_TIP_X0 - (SUN_SMOOTH_X0 - SUN_TIP_LEN)) < 0.05
assert abs(SUN_THREAD_X0 - (SUN_SMOOTH_X0 + SUN_SMOOTH_LEN)) < 0.05
assert abs(SUN_AFT_X - (SUN_SMOOTH_X0 + (SUN_TOTAL_LEN - SUN_TIP_LEN))) < 0.05
assert BATT_Z0 >= WALL - 0.05, "battery pocket intersects floor"
assert BATT_Z0 + BATT_POCKET_Z <= OUTER_H - WALL + 0.05, "battery pocket intersects top"
assert SUN_RECESS_BOSS_LEN <= SUN_RECESS_DEPTH - 2.45, "aft pin still too long for print-1"
assert NOSE_LIP_WALL >= 1.5, "nose lip thinner than print-1 survival"
assert CLAMP_CLEAR >= 0.20, "clamp bore tighter than print-1 allowed"
for _n, _b in BOARDS.items():
    for _z in (_b["z0"], _b["z0"] + _b["zl"]):
        _yi = ellipse_y_plus(_z, WALL)
        assert _yi >= Y_LAND + 1.0, (
            f"{_n} z={_z:.1f}: inner +Y {_yi:.1f} < wall land {Y_LAND}+1"
        )
    assert len(board_standoffs(_b)) >= 2, f"{_n} needs ≥2 M2.5 standoffs (I8)"
    for _hx, _hz in board_standoffs(_b):
        _z = _b["z0"] + _hz
        _yi = ellipse_y_plus(_z, WALL)
        assert _yi >= Y_LAND + 1.0, (
            f"{_n} standoff z={_z:.1f}: inner +Y {_yi:.1f} < wall land {Y_LAND}+1"
        )

# --- panel hardware (L6 locked 2026-08-13) ----------------------------------
# SparkFun COM-08837 = E-Switch R1966A SPST right-angle rocker, snap-in.
#   WALL=2.5 mm → datasheet X=19.5–19.6 mm, height 13.0 mm (2.0–3.0 mm band).
# SparkFun CAB-15464 Micro-B panel 6″, M3 ears 17 mm, 14 mm screws + nuts inside.
# 5 mm chrome ABS LED holder (cnflin / generic): Ø8 mm panel hole, Ø8.2 FDM.
# Cluster on a local planar +Y pad (tall ellipse is too curved for snap-in/flange).
# X order, forward→aft: charge LED | rocker | power LED | USB  (charge LED cannot
# sit aft of USB — Pro Micro upper clamp posts live there).
PANEL_Z = 66.0
SW_CUT_X = 19.6
SW_CUT_Z = 13.0
SW_X = 94.0
SW_Z = PANEL_Z
USB_EAR_PITCH = 17.0
USB_WIN_X = 10.5
USB_WIN_Z = 7.5
USB_X = BABY_X0 + 0.5 * BABY_L
USB_Z = PANEL_Z
M3_CLR_D = 3.3
LED_HOLE_D = 8.2
LED_PWR_X = 114.0  # red: pod powered (VOUT)
LED_CHG_X = 72.0  # blue: charging (!CHG!), forward of the rocker
LED_Z = PANEL_Z
PANEL_T = WALL
PAD_M_X = 3.0
PAD_M_Z_LO = 2.0
PAD_M_Z_HI = 2.0
# Flush with the ellipse at the bottom of the rocker (widest station in the pad).
PANEL_Y = round(ellipse_y_plus(SW_Z - SW_CUT_Z / 2, 0.0), 2)
assert 2.0 <= WALL <= 3.0, "R1966A snap-in X=19.6 is for 2.0–3.0 mm panels"
assert abs(USB_X - (BABY_X0 + 0.5 * BABY_L)) < 0.05
assert SW_Z + SW_CUT_Z / 2 + 1.5 < OUTER_H - WALL, "rocker too close to the flat top"
assert PANEL_Y <= RIGHT_EXTENT + 0.05, "panel pad wider than +Y extent"
assert LED_CHG_X + LED_HOLE_D / 2 + 1.5 < SW_X - SW_CUT_X / 2
assert SW_X + SW_CUT_X / 2 + 1.5 < LED_PWR_X - LED_HOLE_D / 2
assert LED_PWR_X + LED_HOLE_D / 2 + 1.5 < USB_X - USB_EAR_PITCH / 2 - M3_CLR_D / 2


def usb_ear_xz() -> list[tuple[float, float]]:
    half = USB_EAR_PITCH / 2
    return [(USB_X - half, USB_Z), (USB_X + half, USB_Z)]


def panel_pad_xz() -> tuple[float, float, float, float]:
    """x0, x1, z0, z1 of the planar +Y panel pad (margin around all cutouts)."""
    xs = [
        SW_X - SW_CUT_X / 2,
        SW_X + SW_CUT_X / 2,
        USB_X - USB_WIN_X / 2,
        USB_X + USB_WIN_X / 2,
        LED_PWR_X - LED_HOLE_D / 2,
        LED_PWR_X + LED_HOLE_D / 2,
        LED_CHG_X - LED_HOLE_D / 2,
        LED_CHG_X + LED_HOLE_D / 2,
    ]
    for ex, _ez in usb_ear_xz():
        xs.append(ex - M3_CLR_D / 2)
        xs.append(ex + M3_CLR_D / 2)
    zs = [
        SW_Z - SW_CUT_Z / 2,
        SW_Z + SW_CUT_Z / 2,
        USB_Z - USB_WIN_Z / 2,
        USB_Z + USB_WIN_Z / 2,
        LED_Z - LED_HOLE_D / 2,
        LED_Z + LED_HOLE_D / 2,
    ]
    return (
        min(xs) - PAD_M_X,
        max(xs) + PAD_M_X,
        min(zs) - PAD_M_Z_LO,
        max(zs) + PAD_M_Z_HI,
    )


_PAD_X0, _PAD_X1, _PAD_Z0, _PAD_Z1 = panel_pad_xz()
assert _PAD_Z1 < OUTER_H - 0.8, f"panel pad z1={_PAD_Z1:.1f} hits the flat top"
for _n, _b in BOARDS.items():
    for _hx, _hz in board_standoffs(_b):
        _px = _b["x0"] + _hx
        _pz = _b["z0"] + _hz
        if _PAD_X0 - BOARD_POST_D / 2 <= _px <= _PAD_X1 + BOARD_POST_D / 2:
            assert _pz + BOARD_POST_D / 2 + 0.8 <= _PAD_Z0, (
                f"{_n} standoff at z={_pz:.1f} overlaps panel pad z0={_PAD_Z0:.1f}"
            )
print(
    f"panel pad y={PANEL_Y:.2f} (t={PANEL_T:.1f}) z={_PAD_Z0:.1f}..{_PAD_Z1:.1f} "
    f"x={_PAD_X0:.1f}..{_PAD_X1:.1f}; "
    f"CHG LED @{LED_CHG_X:.0f} / rocker @{SW_X:.0f} / PWR LED @{LED_PWR_X:.0f} / "
    f"USB @{USB_X:.1f} z={PANEL_Z:.0f}"
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


def wall_standoff_solid(x: float, z: float) -> cq.Workplane:
    """Raised boss from the PCB plane into the wall land (holes cut later)."""
    outer = full_body_solid(inset=0.0)
    h = STANDOFF_H + WALL_LAND_T + 3.0
    return _cyl_y(x, z, Y_PCB, h, BOARD_POST_D / 2).intersect(outer)


def insert_stack_cuts(x: float, z: float, y_face: float, meat: float) -> list[cq.Workplane]:
    """
    Hub-case insert stack, cut from y_face toward +Y (into PETG):

      heat-set pilot (INS_HOLE_D × INS_DEPTH)
      then narrower screw relief (SCREW_RELIEF_D) continuing past the insert
      leaving ≥ SCREW_RELIEF_FLOOR of PETG before the outer skin.
    """
    outer_y = ellipse_y_plus(z, 0.0)
    available = min(meat, max(0.0, outer_y - y_face))
    cuts = [_cyl_y(x, z, y_face - 0.08, INS_DEPTH + 0.15, INS_HOLE_D / 2)]
    relief = min(INS_DEPTH + SCREW_RELIEF_EXTRA, available - SCREW_RELIEF_FLOOR)
    if relief > INS_DEPTH + 0.05:
        cuts.append(_cyl_y(x, z, y_face - 0.08, relief + 0.15, SCREW_RELIEF_D / 2))
    return cuts


def board_relief_depth(z: float) -> float:
    """How far the screw-relief bore goes from Y_PCB toward the skin."""
    available = ellipse_y_plus(z, 0.0) - Y_PCB
    return min(
        INS_DEPTH + SCREW_RELIEF_EXTRA,
        STANDOFF_H + WALL_LAND_T - SCREW_RELIEF_FLOOR,
        available - SCREW_RELIEF_FLOOR,
    )


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


def _bottom_chord_half_w(inset: float) -> float:
    """Half-width of the flat-bottom chord after flats at z=inset."""
    ry = max(SECTION_RY - inset, 1.0)
    rz = max(SECTION_RZ - inset, 1.0)
    factor = (inset - SECTION_ZC) / rz
    return ry * math.sqrt(max(0.0, 1.0 - factor * factor))


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


def _loft_ogive_nose(inset: float, mouth_r: float) -> cq.Workplane:
    """
    Nose fairs into the SUN tip: mouth circle (≈ tip OD + thin lip) on the
    pitot axis → full mid ellipse at NOSE_FAIR_LEN, held a few mm into mid
    so the junction is a volume overlap (no freestream mid butt).
    """
    ry = max(SECTION_RY - inset, 3.0)
    rz = max(SECTION_RZ - inset, 3.0)
    zc_mid = SECTION_ZC
    mouth_r = max(mouth_r, 2.0)
    tip_yc = NOSE_TIP_YC
    tip_zc = PITOT_AXIS_Z
    n = OGIVE_STATIONS
    x_full = NOSE_FAIR_LEN
    x_hold = NOSE_FAIR_LEN + 2.5  # into mid
    s = (
        cq.Workplane("YZ")
        .workplane(offset=inset)
        .center(tip_yc, tip_zc)
        .circle(mouth_r)
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
                max(mouth_r + (ry - mouth_r) * sc, 2.0),
                max(mouth_r + (rz - mouth_r) * sc, 2.0),
            )
        )
        prev_x, prev_yc, prev_zc = x, yc, zc
    s = (
        s.workplane(offset=x_hold - prev_x)
        .center(SECTION_YC - prev_yc, zc_mid - prev_zc)
        .ellipse(ry, rz)
    )
    return s.loft(ruled=False)


def _loft_ogive_tail(inset: float, _unused_tip_r: float = 0.0) -> cq.Workplane:
    """
    Convex rounded stern: quarter-ellipse radius law (blunt aft body), ending
    on a centred circle.  Kept OCCT-boolean-safe (no sphere fuse, no pin tip).
    """
    ry = max(SECTION_RY - inset, 3.0)
    rz = max(SECTION_RZ - inset, 3.0)
    zc_mid = SECTION_ZC
    tip_yc = SECTION_YC  # centred stern — clean circular end rim
    tip_zc = zc_mid
    x_hold = MID_END_X - 2.5
    x_tip = OUTER_L - inset
    end_r = max(min(0.40 * min(ry, rz), 12.0), 9.5)
    n = OGIVE_STATIONS + 2

    def stern_scale(t: float) -> float:
        t = max(0.0, min(1.0, t))
        return math.sqrt(max(0.0, 1.0 - t * t))

    s = (
        cq.Workplane("YZ")
        .workplane(offset=x_hold)
        .center(SECTION_YC, zc_mid)
        .ellipse(ry, rz)
    )
    prev_x, prev_yc, prev_zc = x_hold, SECTION_YC, zc_mid
    s = (
        s.workplane(offset=MID_END_X - prev_x)
        .center(0, 0)
        .ellipse(ry, rz)
    )
    prev_x = MID_END_X
    t_end = math.sqrt(max(0.0, 1.0 - (end_r / max(ry, end_r + 0.1)) ** 2))
    t_end = max(0.55, min(0.82, t_end))
    for i in range(1, n):
        t = t_end * i / (n - 1)
        sc = stern_scale(t)
        x = MID_END_X + (x_tip - MID_END_X) * (i / (n - 1))
        yc = SECTION_YC + (tip_yc - SECTION_YC) * (i / (n - 1))
        zc = zc_mid + (tip_zc - zc_mid) * (i / (n - 1))
        rr = max(ry * sc, end_r)
        rz_i = max(rz * sc, end_r)
        if i == n - 1:
            s = (
                s.workplane(offset=x - prev_x)
                .center(yc - prev_yc, zc - prev_zc)
                .circle(end_r)
            )
        else:
            s = (
                s.workplane(offset=x - prev_x)
                .center(yc - prev_yc, zc - prev_zc)
                .ellipse(rr, rz_i)
            )
        prev_x, prev_yc, prev_zc = x, yc, zc
    return s.loft(ruled=False)


def _panel_pad_slab(y_face: float) -> cq.Workplane:
    """XZ rectangle ending at y_face; thick enough to merge with the ellipse."""
    x0, x1, z0, z1 = panel_pad_xz()
    thick = 18.0
    return (
        cq.Workplane("XY")
        .transformed(offset=(x0, y_face - thick, z0))
        .box(x1 - x0, thick, z1 - z0, centered=(False, False, False))
    )


def full_body_solid(inset: float = 0.0) -> cq.Workplane:
    """Mid + ogive nose/tail: shared ellipse, flat-chopped top/bottom."""
    mouth_r = max(NOSE_MOUTH_R - 0.35 * inset, 2.0)
    body_x0 = NOSE_FAIR_LEN
    body_len = MID_END_X - body_x0
    mid = _flat_caps(_ellipse_mid(inset, body_x0, body_len), inset)
    nose = _flat_caps(_loft_ogive_nose(inset, mouth_r), inset)
    tail = _flat_caps(_loft_ogive_tail(inset), inset)
    body = mid.union(nose).union(tail)
    # Local planar +Y pad so R1966A / USB flange see 2.5 mm of flat PETG.
    y_face = PANEL_Y - inset
    if y_face > Y_LAND + 1.0:
        body = body.union(_panel_pad_slab(y_face))
        body = _flat_caps(body, inset)
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

    # Open mating flange (I4): perimeter rail + local screw bosses.  The bay
    # centre stays open so the battery and boards install from the seam before
    # close-up (S1).  A solid bulkhead across the face blocks that path.
    fx0, fx1 = FLANGE_X0, FLANGE_X1
    flen = fx1 - fx0
    z0 = WALL
    z_h = OUTER_H - 2 * WALL
    if side > 0:
        flange = (
            cq.Workplane("XY")
            .transformed(offset=(fx0, 0, z0))
            .box(flen, FLANGE_W, z_h, centered=(False, False, False))
        )
        open_bay = (
            cq.Workplane("XY")
            .transformed(offset=(fx0 + FLANGE_RAIL, -0.05, z0 + FLANGE_RAIL))
            .box(
                flen - 2 * FLANGE_RAIL,
                FLANGE_W + 0.2,
                z_h - 2 * FLANGE_RAIL,
                centered=(False, False, False),
            )
        )
        flange = flange.cut(open_bay)
        for fx, fz in FLANGE_SCREWS:
            flange = flange.union(
                _cyl_y(fx, fz, -0.05, FLANGE_W + 0.1, BOSS_D / 2)
            )
    else:
        flange = (
            cq.Workplane("XY")
            .transformed(offset=(fx0, -FLANGE_W, z0))
            .box(flen, FLANGE_W, z_h, centered=(False, False, False))
        )
        open_bay = (
            cq.Workplane("XY")
            .transformed(
                offset=(fx0 + FLANGE_RAIL, -FLANGE_W - 0.05, z0 + FLANGE_RAIL)
            )
            .box(
                flen - 2 * FLANGE_RAIL,
                FLANGE_W + 0.2,
                z_h - 2 * FLANGE_RAIL,
                centered=(False, False, False),
            )
        )
        flange = flange.cut(open_bay)
        for fx, fz in FLANGE_SCREWS:
            flange = flange.union(
                _cyl_y(fx, fz, 0.05, -(FLANGE_W + 0.1), BOSS_D / 2)
            )
    # Clip to outer envelope so rectangular blanks don't poke out of the ogives.
    flange = flange.intersect(outer)
    body = body.union(flange)

    # Mating-face seal groove on the right-half rails (thin rubber / O-cord).
    # A wipe of RTV on the flange face is an acceptable alternative / backup.
    if side > 0:
        g_z0 = z0 + 0.5 * (FLANGE_RAIL - GASKET_W)
        g_z1 = OUTER_H - WALL - FLANGE_RAIL + 0.5 * (FLANGE_RAIL - GASKET_W)
        g_x0 = fx0 + 0.5 * (FLANGE_RAIL - GASKET_W)
        g_x1 = fx1 - 0.5 * (FLANGE_RAIL + GASKET_W)
        groove = (
            cq.Workplane("XY")
            .transformed(offset=(fx0 + FLANGE_RAIL * 0.25, -0.05, g_z0))
            .box(flen - FLANGE_RAIL * 0.5, GASKET_D + 0.1, GASKET_W,
                 centered=(False, False, False))
        )
        groove2 = (
            cq.Workplane("XY")
            .transformed(offset=(fx0 + FLANGE_RAIL * 0.25, -0.05, g_z1))
            .box(flen - FLANGE_RAIL * 0.5, GASKET_D + 0.1, GASKET_W,
                 centered=(False, False, False))
        )
        groove3 = (
            cq.Workplane("XY")
            .transformed(offset=(g_x0, -0.05, z0 + FLANGE_RAIL * 0.25))
            .box(
                GASKET_W,
                GASKET_D + 0.1,
                z_h - FLANGE_RAIL * 0.5,
                centered=(False, False, False),
            )
        )
        groove4 = (
            cq.Workplane("XY")
            .transformed(offset=(g_x1, -0.05, z0 + FLANGE_RAIL * 0.25))
            .box(
                GASKET_W,
                GASKET_D + 0.1,
                z_h - FLANGE_RAIL * 0.5,
                centered=(False, False, False),
            )
        )
        body = body.cut(groove).cut(groove2).cut(groove3).cut(groove4)

    return body


def add_pitot_cradle(body: cq.Workplane, side: int) -> cq.Workplane:
    """
    SUN-B mount (no separate saddle STL):

      1. Forward stop — tip-only nose BH; Ø10.65 seats at x=SHOULDER_BH_T
      2. Integral split clamp — thick L/R land + snug bore on knurled band
      3. Aft boss — seats end-B blind recess
      4. Barb bay (+Z) and right-half hose escape

    Closing the clamshell flange screws supplies the clamp press.
    """
    outer = full_body_solid(inset=0.0)

    def _side_plate(x0: float, xlen: float) -> cq.Workplane:
        """
        Cradle bulkhead blank, clipped to CRADLE_LAND_Y so plates stay around
        the pitot bore and do not span the electronics bay (board collisions).
        """
        if side > 0:
            y_span = min(CRADLE_LAND_Y, RIGHT_EXTENT - WALL - 0.4)
            plate = (
                cq.Workplane("XY")
                .transformed(offset=(x0, 0.0, WALL))
                .box(xlen, y_span, OUTER_H - 2 * WALL, centered=(False, False, False))
            )
        else:
            y_span = min(CRADLE_LAND_Y, LEFT_EXTENT - WALL - 0.4)
            plate = (
                cq.Workplane("XY")
                .transformed(offset=(x0, -y_span, WALL))
                .box(
                    xlen,
                    max(y_span, 1.0),
                    OUTER_H - 2 * WALL,
                    centered=(False, False, False),
                )
            )
        return plate.intersect(outer)

    # Nose shoulder bulkhead: tip-only bore; Ø10.65 seats on aft face (x=SHOULDER_BH_T).
    body = _union_if_solid(body, _side_plate(0.0, SHOULDER_BH_T))
    # Tip clearance through bulkhead + protrusion (never open this to smooth OD).
    body = body.cut(
        x_cylinder(
            CRADLE_R_TIP,
            SUN_TIP_LEN + SHOULDER_BH_T + 2.0,
            SUN_TIP_X0 - 1.0,
            0.0,
            PITOT_AXIS_Z,
        )
    )
    # Ø10.65 smooth barrel — starts aft of the shoulder seat face.
    body = body.cut(
        x_cylinder(
            CRADLE_R_SMOOTH,
            SUN_SMOOTH_LEN + 0.4,
            SUN_SMOOTH_X0,
            0.0,
            PITOT_AXIS_Z,
        )
    )
    # Knurled band — snug bore (clamp land added below).
    body = body.cut(
        x_cylinder(
            CRADLE_R_CLAMP,
            SUN_THREAD_LEN + 0.6,
            SUN_THREAD_X0 - 0.3,
            0.0,
            PITOT_AXIS_Z,
        )
    )
    # Matte barb barrel to aft face (slip).
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
                Y_PCB + 2.0,
                14.0,
                centered=(False, False, False),
            )
        )
        body = body.cut(hose)

    # Mid-smooth bulkhead (secondary bearing on Ø10.65).
    x_sm = SUN_SMOOTH_X0 + SUN_SMOOTH_LEN * 0.55
    body = _union_if_solid(body, _side_plate(x_sm, 3.0))
    body = body.cut(x_cylinder(CRADLE_R_SMOOTH, 5.0, x_sm - 1.0, 0.0, PITOT_AXIS_Z))

    # Integral split clamp land on the knurled band (thick, snug bore).
    clamp = _side_plate(CLAMP_X0, CLAMP_LEN)
    # Keep clamp meat near the bore; trim far electronics deck volume on right.
    clamp_core = x_cylinder(
        CLAMP_R_OUTER, CLAMP_LEN + 0.4, CLAMP_X0 - 0.2, 0.0, PITOT_AXIS_Z
    )
    clamp = clamp.intersect(clamp_core)
    body = _union_if_solid(body, clamp)
    body = body.cut(
        x_cylinder(CRADLE_R_CLAMP, CLAMP_LEN + 1.0, CLAMP_X0 - 0.5, 0.0, PITOT_AXIS_Z)
    )

    # Bulkhead just forward of aft face (before barbs end).
    x_aft_pre = SUN_AFT_X - 8.0
    aft_pre = _side_plate(x_aft_pre, 3.0).cut(barb_bay)
    body = _union_if_solid(body, aft_pre)
    body = body.cut(
        x_cylinder(CRADLE_R_BARREL, 5.0, x_aft_pre - 1.0, 0.0, PITOT_AXIS_Z)
    )

    # Aft bulkhead + locating boss into end-B blind recess.
    aft_bh_x = SUN_AFT_X + 0.2
    body = _union_if_solid(body, _side_plate(aft_bh_x, 3.0))
    boss = x_cylinder(
        SUN_RECESS_BOSS_D / 2,
        SUN_RECESS_BOSS_LEN + 1.5,
        SUN_AFT_X - SUN_RECESS_BOSS_LEN,
        0.0,
        PITOT_AXIS_Z,
    )
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
    """Simple stepped solid for assembly STEP / QC renders (not printed)."""
    tip = x_cylinder(
        SUN_TIP_OD / 2, SUN_TIP_LEN, SUN_TIP_X0, 0.0, PITOT_AXIS_Z
    )
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


def _mating_flange_protect() -> cq.Workplane:
    """
    Perimeter rails + screw bosses on both halves.  Subtracted from the battery
    pocket tool so a full-height pack recess cannot erase the open flange (I4).
    """
    fx0, fx1 = FLANGE_X0, FLANGE_X1
    flen = fx1 - fx0
    z0 = WALL
    z_h = OUTER_H - 2 * WALL
    parts: list[cq.Workplane] = []
    for y0, y_sign in ((0.0, +1.0), (-FLANGE_W, -1.0)):
        rail = (
            cq.Workplane("XY")
            .transformed(offset=(fx0, y0, z0))
            .box(flen, FLANGE_W, z_h, centered=(False, False, False))
        )
        open_bay = (
            cq.Workplane("XY")
            .transformed(offset=(fx0 + FLANGE_RAIL, y0 - 0.05, z0 + FLANGE_RAIL))
            .box(
                flen - 2 * FLANGE_RAIL,
                FLANGE_W + 0.2,
                z_h - 2 * FLANGE_RAIL,
                centered=(False, False, False),
            )
        )
        rail = rail.cut(open_bay)
        for fx, fz in FLANGE_SCREWS:
            rail = rail.union(
                _cyl_y(fx, fz, 0.0 if y_sign > 0 else 0.0, y_sign * FLANGE_W, BOSS_D / 2)
            )
        parts.append(rail)
    out = parts[0].union(parts[1])
    return out.intersect(full_body_solid(inset=0.0))


def add_battery_pocket(body: cq.Workplane) -> cq.Workplane:
    """
    Centerline pack recess.  The LiPo is installed from the open mating face
    before the clamshell is closed — this cut only notches interior/flange
    material.  It must never pierce the outer skin (a raw box cut previously
    punched a freestream slot through the thin left bottom and the floor/roof).
    Top/bottom flange rails and screw bosses are preserved (I4).
    """
    outer = full_body_solid(inset=0.0)
    inner = full_body_solid(inset=WALL)
    wall = outer.cut(inner)
    pocket = (
        cq.Workplane("XY")
        .transformed(offset=(BATT_X0, BATT_Y0, BATT_Z0))
        .box(BATT_POCKET_X, BATT_POCKET_Y, BATT_POCKET_Z, centered=(False, False, False))
    )
    # Interior notch only: inside outer envelope, but not through the skin.
    cut_tool = pocket.intersect(outer).cut(wall)
    try:
        cut_tool = cut_tool.cut(_mating_flange_protect())
    except Exception:
        pass
    try:
        if cut_tool.val().Volume() < 1.0:
            return body
    except Exception:
        return body
    return body.cut(cut_tool)


def _cyl_y(x: float, z: float, y0: float, dy: float, r: float) -> cq.Workplane:
    """
    Solid cylinder radius r along ±Y from y0 (length |dy|).

    CadQuery's "XZ" workplane has normal −Y, so offset/extrude sign flips are
    easy to get wrong and previously cut flange screws into empty space.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    direction = 1.0 if dy >= 0.0 else -1.0
    ax = gp_Ax2(gp_Pnt(x, y0, z), gp_Dir(0.0, direction, 0.0))
    return cq.Workplane().add(
        cq.Shape.cast(BRepPrimAPI_MakeCylinder(ax, r, abs(dy)).Shape())
    )


def add_flange_fasteners(body: cq.Workplane, side: int) -> cq.Workplane:
    """
    Clamshell fasteners: M2.5 screws through the left cover into heat-set
    inserts in the right (electronics) half.
    """
    for (fx, fz) in FLANGE_SCREWS:
        if side > 0:
            # Heat-set pilots in the right flange, axis +Y from the seam.
            body = body.cut(_cyl_y(fx, fz, -0.05, INS_DEPTH + 0.1, INS_HOLE_D / 2))
            relief_depth = min(
                INS_DEPTH + SCREW_RELIEF_EXTRA, FLANGE_W - SCREW_RELIEF_FLOOR
            )
            if relief_depth > INS_DEPTH + 0.05:
                body = body.cut(
                    _cyl_y(fx, fz, -0.05, relief_depth + 0.1, SCREW_RELIEF_D / 2)
                )
        else:
            # Clearance through the left cover flange; CB on the −Y (exterior) face.
            body = body.cut(
                _cyl_y(fx, fz, 0.1, -(FLANGE_W + 0.3), LID_SCREW_D / 2)
            )
            body = body.cut(
                _cyl_y(
                    fx,
                    fz,
                    -FLANGE_W + 0.05,
                    -(LID_CB_DEPTH + 0.15),
                    LID_CB_D / 2,
                )
            )
    return body


def add_electronics_wall(body: cq.Workplane) -> cq.Workplane:
    """
    Right-half +Y wall land + raised standoffs with hub-case insert/relief.

    Heat-set from the open mating face (iron along +Y into the inboard face).
    Insert + screw-relief holes are cut from the assembled body so they
    continue through the land, not only the boss.
    """
    z_lo = min(b["z0"] for b in BOARDS.values()) - 4.0
    z_hi = max(b["z0"] + b["zl"] for b in BOARDS.values()) + 4.0
    x_lo = min(b["x0"] for b in BOARDS.values()) - 4.0
    x_hi = max(b["x0"] + b["xl"] for b in BOARDS.values()) + 4.0
    land = (
        cq.Workplane("XY")
        .transformed(offset=(x_lo, Y_LAND, z_lo))
        .box(
            x_hi - x_lo,
            WALL_LAND_T + 4.0,
            z_hi - z_lo,
            centered=(False, False, False),
        )
    )
    land = land.intersect(full_body_solid(inset=0.0))
    body = _union_if_solid(body, land)

    meat = STANDOFF_H + WALL_LAND_T
    for b in BOARDS.values():
        for hx, hz in board_standoffs(b):
            x = b["x0"] + hx
            z = b["z0"] + hz
            body = _union_if_solid(body, wall_standoff_solid(x, z))
            for cut in insert_stack_cuts(x, z, Y_PCB, meat):
                body = body.cut(cut)
    return body


def _static_bay_box() -> tuple[float, float, float, float, float, float]:
    """x0,x1,y0,y1,z0,z1 of the isolated static plenum (BMP + mag)."""
    wx0, wx1, wz0, wz1 = _static_window()
    x0 = wx0 - BAY_WALL
    x1 = wx1 + BAY_WALL
    z0 = wz0 - STATIC_FRAME_LIP
    z1 = wz1 + STATIC_FRAME_LIP
    y0 = max(CRADLE_LAND_Y + 1.5, Y_PCB - PCB_T - COMP_H - 4.0)
    y1 = RIGHT_EXTENT
    return x0, x1, y0, y1, z0, z1


def _static_window() -> tuple[float, float, float, float]:
    """XZ opening in the -Y bay wall so irons/boards fit; cover closes it."""
    bmp = BOARDS["BMP581"]
    mag = BOARDS["MAG"]
    m = STATIC_WINDOW_MARGIN
    wx0 = min(bmp["x0"], mag["x0"]) - m
    wx1 = max(bmp["x0"] + bmp["xl"], mag["x0"] + mag["xl"]) + m
    wz0 = min(bmp["z0"], mag["z0"]) - m
    wz1 = max(bmp["z0"] + bmp["zl"], mag["z0"] + mag["zl"]) + m
    return wx0, wx1, wz0, wz1


def static_hole_centers() -> list[tuple[float, float]]:
    bmp = BOARDS["BMP581"]
    cx = bmp["x0"] + 0.5 * bmp["xl"]
    cz = bmp["z0"] + 0.5 * bmp["zl"]
    hx0 = cx - (STATIC_HOLE_COLS - 1) * STATIC_HOLE_PITCH_X / 2
    hz0 = cz - (STATIC_HOLE_ROWS - 1) * STATIC_HOLE_PITCH_Z / 2
    return [
        (hx0 + i * STATIC_HOLE_PITCH_X, hz0 + j * STATIC_HOLE_PITCH_Z)
        for i in range(STATIC_HOLE_COLS)
        for j in range(STATIC_HOLE_ROWS)
    ]


def _static_cover_screws() -> list[tuple[float, float]]:
    """Cover screws on the ±Z frame lips (not the thin ±X walls — PM is close)."""
    wx0, wx1, wz0, wz1 = _static_window()
    dx = 6.0
    dz = 4.0
    return [
        (wx0 + dx, wz0 - dz),
        (wx1 - dx, wz0 - dz),
        (wx0 + dx, wz1 + dz),
        (wx1 - dx, wz1 + dz),
    ]


def add_static_bay(body: cq.Workplane) -> cq.Workplane:
    """
    Isolated static plenum: ±X/±Z walls + thick -Y frame with a tool window.

    Static holes in the +Y skin open only into this box.  After heat-set and
    BMP/mag install, `static_cover.stl` screws onto the frame (foam/RTV gasket)
    so moisture or a leaky static line cannot flood the electronics bay.
    Qwiic leaves through a gland slot in the cover.
    """
    outer = full_body_solid(inset=0.0)
    x0, x1, y0, y1, z0, z1 = _static_bay_box()
    wx0, wx1, wz0, wz1 = _static_window()
    zh = z1 - z0
    yw = y1 - y0
    xw = x1 - x0

    def _box(ox, oy, oz, sx, sy, sz) -> cq.Workplane:
        return (
            cq.Workplane("XY")
            .transformed(offset=(ox, oy, oz))
            .box(sx, sy, sz, centered=(False, False, False))
            .intersect(outer)
        )

    body = _union_if_solid(body, _box(x0, y0, z0, BAY_WALL, yw, zh))
    body = _union_if_solid(body, _box(x1 - BAY_WALL, y0, z0, BAY_WALL, yw, zh))
    body = _union_if_solid(body, _box(x0, y0, z0, xw, yw, BAY_WALL))
    body = _union_if_solid(body, _box(x0, y0, z1 - BAY_WALL, xw, yw, BAY_WALL))
    # Thick -Y frame (insert meat for the cover) minus the tool window
    plate = _box(x0, y0, z0, xw, STATIC_FRAME_T, zh)
    window = (
        cq.Workplane("XY")
        .transformed(offset=(wx0, y0 - 0.2, wz0))
        .box(wx1 - wx0, STATIC_FRAME_T + 0.5, wz1 - wz0, centered=(False, False, False))
    )
    body = _union_if_solid(body, plate.cut(window))
    for fx, fz in _static_cover_screws():
        for cut in insert_stack_cuts(fx, fz, y0, STATIC_FRAME_T):
            body = body.cut(cut)
    bmp = BOARDS["BMP581"]
    cx = bmp["x0"] + 0.5 * bmp["xl"]
    cz = bmp["z0"] + 0.5 * bmp["zl"]
    hx0 = cx - (STATIC_HOLE_COLS - 1) * STATIC_HOLE_PITCH_X / 2
    hz0 = cz - (STATIC_HOLE_ROWS - 1) * STATIC_HOLE_PITCH_Z / 2
    for i in range(STATIC_HOLE_COLS):
        for j in range(STATIC_HOLE_ROWS):
            body = body.cut(
                _cyl_y(
                    hx0 + i * STATIC_HOLE_PITCH_X,
                    hz0 + j * STATIC_HOLE_PITCH_Z,
                    RIGHT_EXTENT + 0.2,
                    -(WALL + WALL_LAND_T + 4.0),
                    STATIC_HOLE_D / 2,
                )
            )
    return body


def build_pm_tray() -> cq.Workplane:
    """
    Printed tray for the castellated Pro Micro (no OEM holes).  Four M2.5
    clearance holes line up with clamp_posts (Z-overhangs — Baby is 1 mm in −X).
    PCB drops into a shallow pocket; screws clamp the tray to the standoffs.
    """
    b = BOARDS["PROMICRO"]
    t = 2.2
    lip_z = 5.0
    cover = (
        cq.Workplane("XY")
        .transformed(offset=(b["x0"], 0.0, b["z0"] - lip_z))
        .box(b["xl"], t, b["zl"] + 2 * lip_z, centered=(False, False, False))
    )
    pocket = (
        cq.Workplane("XY")
        .transformed(offset=(b["x0"] + 0.4, t - 1.2, b["z0"] + 0.4))
        .box(b["xl"] - 0.8, 1.4, b["zl"] - 0.8, centered=(False, False, False))
    )
    cover = cover.cut(pocket)
    for hx, hz in board_standoffs(b):
        fx, fz = b["x0"] + hx, b["z0"] + hz
        cover = cover.cut(_cyl_y(fx, fz, -0.1, t + 0.3, LID_SCREW_D / 2))
        cover = cover.cut(_cyl_y(fx, fz, -0.05, LID_CB_DEPTH, LID_CB_D / 2))
    return cover


def build_static_cover() -> cq.Workplane:
    """Separate PETG plate: closes the static-bay tool window after install."""
    wx0, wx1, wz0, wz1 = _static_window()
    overlap = STATIC_FRAME_LIP - 1.0
    t = STATIC_COVER_T
    cover = (
        cq.Workplane("XY")
        .transformed(offset=(wx0 - overlap, 0.0, wz0 - overlap))
        .box(
            (wx1 - wx0) + 2 * overlap,
            t,
            (wz1 - wz0) + 2 * overlap,
            centered=(False, False, False),
        )
    )
    # Qwiic gland toward Pro Micro (forward / bottom of the window)
    cover = cover.cut(
        cq.Workplane("XY")
        .transformed(offset=(wx0 + 3.0, -0.1, wz0 + 3.0))
        .box(6.0, t + 0.3, 4.0, centered=(False, False, False))
    )
    for fx, fz in _static_cover_screws():
        cover = cover.cut(_cyl_y(fx, fz, -0.1, t + 0.3, LID_SCREW_D / 2))
        cover = cover.cut(_cyl_y(fx, fz, -0.05, LID_CB_DEPTH, LID_CB_D / 2))
    return cover


def add_panel_cutouts(body: cq.Workplane) -> cq.Workplane:
    """
    Cluster: +Y skin mid-body (S1 allow-list), above the boards:

      • COM-08837 / R1966A snap-in rocker (SYSOFF)
      • CAB-15464 eared Micro-B (M3 through + nuts inside, RTV under flange)
      • 2× Ø8.2 holes for 5 mm chrome LED holders (power red, charge blue)

    The electronics land would refill the pad to ~8 mm — too thick for R1966A
    clips (max 3 mm). Rebate the interior so the remaining wall is PANEL_T.
    """
    x0, x1, z0, z1 = panel_pad_xz()
    y_in = PANEL_Y - PANEL_T
    rebate = (
        cq.Workplane("XY")
        .transformed(offset=(x0, Y_LAND - 2.0, z0))
        .box(x1 - x0, (y_in - (Y_LAND - 2.0)) + 0.08, z1 - z0, centered=(False, False, False))
    )
    body = body.cut(rebate)

    y0 = Y_LAND - 4.0
    dy = (PANEL_Y + 3.0) - y0

    def _xz_box(cx: float, cz: float, sx: float, sz: float) -> cq.Workplane:
        return (
            cq.Workplane("XY")
            .transformed(offset=(cx - sx / 2, y0, cz - sz / 2))
            .box(sx, dy, sz, centered=(False, False, False))
        )

    body = body.cut(_xz_box(SW_X, SW_Z, SW_CUT_X, SW_CUT_Z))
    body = body.cut(_xz_box(USB_X, USB_Z, USB_WIN_X, USB_WIN_Z))
    for lx, lz in ((LED_PWR_X, LED_Z), (LED_CHG_X, LED_Z)):
        body = body.cut(_cyl_y(lx, lz, PANEL_Y + 2.0, -(PANEL_T + WALL_LAND_T + 8.0), LED_HOLE_D / 2))
    for ex, ez in usb_ear_xz():
        body = body.cut(
            _cyl_y(ex, ez, PANEL_Y + 2.0, -(PANEL_T + WALL_LAND_T + 8.0), M3_CLR_D / 2)
        )
    return body


def build_right() -> cq.Workplane:
    body = hollow_half(+1)
    body = add_pitot_cradle(body, +1)
    body = add_battery_pocket(body)
    body = add_flange_fasteners(body, +1)
    body = add_electronics_wall(body)
    body = add_static_bay(body)
    body = add_panel_cutouts(body)
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


def render_right_interior_png(right: cq.Workplane | None = None) -> str:
    """
    Orthogonal QC view into open pod_right (−Y → +Y): SUN-B + boards + battery
    pocket.  Unique filename per envelope size.  Called from __main__ each regen.
    """
    import numpy as np
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from itertools import product

    def tessellate(wp: cq.Workplane, tol: float = 0.28):
        verts, tris = wp.val().tessellate(tol, 0.2)
        v = np.array([[p.x, p.y, p.z] for p in verts], dtype=np.float64)
        return v[np.array(tris, dtype=np.int32)]

    if right is None:
        right = as_single_solid(build_right(), "pod_right")
    out = f"pod_v2_{OUTER_L:.0f}x{OUTER_W:.0f}x{OUTER_H:.0f}_panel_interior.png"

    palette = {
        "MS4525": (0.15, 0.45, 0.85, 1.0),
        "BOOST": (0.90, 0.30, 0.15, 1.0),
        "BABY": (0.20, 0.70, 0.30, 1.0),
        "PROMICRO": (0.55, 0.30, 0.80, 1.0),
        "BMP581": (0.95, 0.75, 0.10, 1.0),
        "MAG": (0.80, 0.20, 0.55, 1.0),
    }
    # Distinct SUN segments so the Ø8.93→Ø10.65 shoulder stop is obvious.
    sun_segs = (
        (
            x_cylinder(SUN_TIP_OD / 2, SUN_TIP_LEN, SUN_TIP_X0, 0.0, PITOT_AXIS_Z),
            (0.55, 0.58, 0.62, 0.95),
            "SUN tip Ø8.93",
        ),
        (
            x_cylinder(
                SUN_SMOOTH_OD / 2, SUN_SMOOTH_LEN, SUN_SMOOTH_X0, 0.0, PITOT_AXIS_Z
            ),
            (0.25, 0.35, 0.55, 0.98),
            "SUN smooth Ø10.65",
        ),
        (
            x_cylinder(
                SUN_THREAD_MAJOR / 2, SUN_THREAD_LEN, SUN_THREAD_X0, 0.0, PITOT_AXIS_Z
            ).union(
                x_cylinder(
                    SUN_BARREL_OD / 2,
                    SUN_AFT_X - SUN_THREAD_X1,
                    SUN_THREAD_X1,
                    0.0,
                    PITOT_AXIS_Z,
                )
            ),
            (0.40, 0.48, 0.55, 0.95),
            "SUN clamp/barrel",
        ),
    )

    fig = plt.figure(figsize=(16, 7.2), dpi=150)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_proj_type("ortho")

    ax.add_collection3d(
        Poly3DCollection(
            tessellate(right, 0.28),
            facecolors=(0.93, 0.78, 0.42, 0.18),
            edgecolors=(0.35, 0.28, 0.15, 0.12),
            linewidths=0.12,
        )
    )
    for seg, color, _ in sun_segs:
        ax.add_collection3d(
            Poly3DCollection(
                tessellate(seg, 0.35),
                facecolors=color,
                edgecolors=(0.15, 0.15, 0.18, 0.35),
                linewidths=0.2,
            )
        )
    for name, b in BOARDS.items():
        pcb = (
            cq.Workplane("XY")
            .transformed(offset=(b["x0"], Y_PCB - PCB_T, b["z0"]))
            .box(b["xl"], PCB_T, b["zl"], centered=(False, False, False))
        )
        ax.add_collection3d(
            Poly3DCollection(
                tessellate(pcb, 0.5),
                facecolors=palette[name],
                edgecolors=(0.05, 0.05, 0.05, 0.5),
                linewidths=0.3,
            )
        )
        # Raised standoffs (visible gap between land and PCB)
        posts = board_standoffs(b)
        if posts:
            for hx, hz in posts:
                post = _cyl_y(b["x0"] + hx, b["z0"] + hz, Y_PCB, STANDOFF_H, BOARD_POST_D / 2)
                ax.add_collection3d(
                    Poly3DCollection(
                        tessellate(post, 0.6),
                        facecolors=(0.45, 0.32, 0.18, 0.95),
                        edgecolors=(0.2, 0.15, 0.1, 0.4),
                        linewidths=0.15,
                    )
                )
        ax.text(
            b["x0"] + b["xl"] / 2,
            Y_PCB + 2.0,
            b["z0"] + b["zl"] / 2,
            name,
            fontsize=7,
            ha="center",
            va="center",
            color="black",
        )

    route_colors = {
        "hose": (0.10, 0.45, 0.85, 0.95),
        "wire": (0.15, 0.15, 0.15, 0.85),
    }
    for route in build_routes():
        pts = route["pts"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        lw = 2.4 if route["kind"] == "hose" else 1.4
        ax.plot(xs, ys, zs, color=route_colors[route["kind"]], lw=lw, alpha=0.9)
        ax.text(
            xs[-1], ys[-1], zs[-1] + 2.5, route["name"], fontsize=6, color="navy"
        )

    bx0, by0, bz0 = BATT_X0, BATT_Y0, BATT_Z0
    bx1 = bx0 + BATT_POCKET_X
    by1 = by0 + BATT_POCKET_Y
    bz1 = bz0 + BATT_POCKET_Z
    corners = list(product([bx0, bx1], [by0, by1], [bz0, bz1]))
    for i, j in (
        (0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
        (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7),
    ):
        p, q = corners[i], corners[j]
        ax.plot([p[0], q[0]], [p[1], q[1]], [p[2], q[2]], color="black", lw=1.1, alpha=0.85)

    x0, x1, y0, y1, z0, z1 = _static_bay_box()
    bay_corners = list(product([x0, x1], [y0, y1], [z0, z1]))
    for i, j in (
        (0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
        (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7),
    ):
        p, q = bay_corners[i], bay_corners[j]
        ax.plot([p[0], q[0]], [p[1], q[1]], [p[2], q[2]], color="0.25", lw=0.9, ls="--", alpha=0.8)

    baby = BOARDS["BABY"]
    ax.plot(
        [USB_X, USB_X], [Y_LAND, PANEL_Y], [USB_Z, USB_Z],
        color="crimson", lw=2.0, alpha=0.85,
    )
    ax.text(USB_X, RIGHT_EXTENT - 1, USB_Z + 4, "USB", fontsize=6, color="crimson")
    ax.text(SW_X, RIGHT_EXTENT - 1, SW_Z + 4, "SW", fontsize=6, color="crimson")
    ax.text(LED_PWR_X, RIGHT_EXTENT - 1, LED_Z + 4, "LED PWR", fontsize=6, color="crimson")
    ax.text(LED_CHG_X, RIGHT_EXTENT - 1, LED_Z + 4, "LED CHG", fontsize=6, color="crimson")

    for x, label in (
        (SUN_SMOOTH_X0, "shoulder\nseat"),
        (CLAMP_X0, "clamp"),
        (SUN_AFT_X, "aft"),
    ):
        ax.plot(
            [x, x], [0, 8], [PITOT_AXIS_Z, PITOT_AXIS_Z],
            color="crimson", lw=1.0, alpha=0.7,
        )
        ax.text(x, 10, PITOT_AXIS_Z + 6, label, fontsize=6, color="crimson", ha="center")

    ax.set_xlim(SUN_TIP_X0 - 5, OUTER_L + 5)
    ax.set_ylim(-2, RIGHT_EXTENT + 2)
    ax.set_zlim(-2, OUTER_H + 2)
    ax.set_box_aspect((OUTER_L - SUN_TIP_X0 + 10, RIGHT_EXTENT + 4, OUTER_H + 4))
    ax.view_init(elev=0, azim=-90)
    ax.set_xlabel("X aft (mm)")
    ax.set_ylabel("Y right (mm)")
    ax.set_zlabel("Z up (mm)")
    ax.set_title(
        "Right half interior — panel pad + raised standoffs + isolated static bay\n"
        f"SUN tip @ {SUN_TIP_X0:.1f}; Y_LAND={Y_LAND:.0f}; "
        f"standoff {STANDOFF_H:.0f} → Y_PCB={Y_PCB:.0f}"
    )
    ax.grid(True, alpha=0.25)
    handles = [
        Patch(facecolor=(0.93, 0.78, 0.42, 0.5), label="pod_right"),
        *[Patch(facecolor=c[:3], label=lab) for _, c, lab in sun_segs],
        Patch(facecolor="black", label="battery pocket"),
        Patch(facecolor=(0.45, 0.32, 0.18), label="board standoffs"),
        Patch(facecolor="0.4", label="static bay"),
    ] + [Patch(facecolor=palette[n], label=n) for n in palette]
    ax.legend(handles=handles, loc="upper right", fontsize=7.5, framealpha=0.92)
    plt.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {out}")
    return out


def render_routing_png() -> str:
    """2D X–Z (wall face) + X–Y (plan) QC of boards and named routes."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle

    out = f"pod_v2_{OUTER_L:.0f}x{OUTER_W:.0f}x{OUTER_H:.0f}_panel_routing.png"
    palette = {
        "MS4525": "#2673d9",
        "BOOST": "#e64d26",
        "BABY": "#33b34d",
        "PROMICRO": "#8c4dcc",
        "BMP581": "#f2bf1a",
        "MAG": "#cc3399",
    }
    route_style = {
        "hose": dict(color="#1a73e8", lw=2.2),
        "wire": dict(color="#222222", lw=1.3, ls="--"),
    }

    fig, (ax_xz, ax_xy) = plt.subplots(2, 1, figsize=(14, 9), dpi=140)
    for name, b in BOARDS.items():
        ax_xz.add_patch(
            Rectangle(
                (b["x0"], b["z0"]),
                b["xl"],
                b["zl"],
                facecolor=palette[name],
                edgecolor="black",
                alpha=0.85,
                lw=0.6,
            )
        )
        ax_xz.text(
            b["x0"] + b["xl"] / 2,
            b["z0"] + b["zl"] / 2,
            name,
            ha="center",
            va="center",
            fontsize=8,
        )
        for hx, hz in board_standoffs(b):
            ax_xz.add_patch(
                Circle(
                    (b["x0"] + hx, b["z0"] + hz),
                    BOARD_POST_D / 2,
                    facecolor="none",
                    edgecolor="#6b4a28",
                    lw=0.9,
                )
            )
        ax_xy.add_patch(
            Rectangle(
                (b["x0"], Y_PCB - PCB_T - b["comp_h"]),
                b["xl"],
                PCB_T + b["comp_h"],
                facecolor=palette[name],
                edgecolor="black",
                alpha=0.85,
                lw=0.6,
            )
        )
    ax_xy.axhline(Y_LAND, color="0.35", lw=0.9, label="wall land")
    ax_xy.axhline(PANEL_Y, color="crimson", lw=0.8, ls=":", label="panel pad")
    ax_xy.axhline(Y_PCB, color="0.35", lw=0.8, ls="--", label="PCB / standoff face")
    ax_xy.axhline(CRADLE_LAND_Y, color="0.6", lw=0.8, ls=":", label="cradle land")
    x0, x1, y0, y1, z0, z1 = _static_bay_box()
    ax_xz.add_patch(
        Rectangle(
            (x0, z0),
            x1 - x0,
            z1 - z0,
            fill=False,
            edgecolor="0.25",
            lw=1.0,
            ls="--",
            label="static bay",
        )
    )
    wx0, wx1, wz0, wz1 = _static_window()
    ax_xz.add_patch(
        Rectangle(
            (wx0, wz0),
            wx1 - wx0,
            wz1 - wz0,
            fill=False,
            edgecolor="#1a73e8",
            lw=0.8,
            label="iron window",
        )
    )
    px0, px1, pz0, pz1 = panel_pad_xz()
    ax_xz.add_patch(
        Rectangle(
            (px0, pz0),
            px1 - px0,
            pz1 - pz0,
            fill=False,
            edgecolor="0.4",
            lw=0.7,
            ls=":",
            label="panel pad",
        )
    )
    ax_xz.add_patch(
        Rectangle(
            (SW_X - SW_CUT_X / 2, SW_Z - SW_CUT_Z / 2),
            SW_CUT_X,
            SW_CUT_Z,
            fill=False,
            edgecolor="crimson",
            lw=1.0,
            label="rocker",
        )
    )
    ax_xz.add_patch(
        Rectangle(
            (USB_X - USB_WIN_X / 2, USB_Z - USB_WIN_Z / 2),
            USB_WIN_X,
            USB_WIN_Z,
            fill=False,
            edgecolor="crimson",
            lw=1.0,
            label="USB",
        )
    )
    for ex, ez in usb_ear_xz():
        ax_xz.add_patch(Circle((ex, ez), M3_CLR_D / 2, facecolor="none", edgecolor="crimson", lw=0.8))
    for lx, lab in ((LED_PWR_X, "PWR"), (LED_CHG_X, "CHG")):
        ax_xz.add_patch(
            Circle((lx, LED_Z), LED_HOLE_D / 2, facecolor="none", edgecolor="crimson", lw=0.9)
        )
        ax_xz.annotate(lab, (lx, LED_Z + 5), fontsize=7, color="crimson", ha="center")
    ax_xy.add_patch(
        Rectangle(
            (BATT_X0, BATT_Y0),
            BATT_POCKET_X,
            BATT_POCKET_Y,
            fill=False,
            edgecolor="black",
            lw=1.2,
            label="battery",
        )
    )
    for route in build_routes():
        st = route_style[route["kind"]]
        xs = [p[0] for p in route["pts"]]
        ys = [p[1] for p in route["pts"]]
        zs = [p[2] for p in route["pts"]]
        ax_xz.plot(xs, zs, **st)
        ax_xy.plot(xs, ys, **st)
        ax_xz.annotate(route["name"], (xs[-1], zs[-1]), fontsize=7, color=st["color"])

    ax_xz.axvline(SUN_SMOOTH_X0, color="crimson", lw=0.7, alpha=0.6)
    ax_xz.axvline(SUN_AFT_X, color="crimson", lw=0.7, alpha=0.6)
    ax_xz.set_xlim(SUN_TIP_X0 - 5, OUTER_L + 5)
    ax_xz.set_ylim(-2, OUTER_H + 2)
    ax_xz.set_aspect("equal", adjustable="box")
    ax_xz.set_xlabel("X aft (mm)")
    ax_xz.set_ylabel("Z up (mm)")
    ax_xz.set_title("Wall face (X–Z): boards, standoffs, static bay, panel cluster")
    ax_xz.grid(True, alpha=0.25)
    ax_xz.legend(loc="upper right", fontsize=7)

    ax_xy.set_xlim(SUN_TIP_X0 - 5, OUTER_L + 5)
    ax_xy.set_ylim(-LEFT_EXTENT - 2, RIGHT_EXTENT + 2)
    ax_xy.set_aspect("equal", adjustable="box")
    ax_xy.set_xlabel("X aft (mm)")
    ax_xy.set_ylabel("Y right (mm)")
    ax_xy.set_title("Plan (X–Y): standoffs raise PCBs off the land")
    ax_xy.grid(True, alpha=0.25)
    ax_xy.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"wrote {out}")
    return out


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

    cover = as_single_solid(build_static_cover(), "static_cover")
    cover_print = cover.rotate((0, 0, 0), (1, 0, 0), -90)
    bb = cover_print.val().BoundingBox()
    cover_print = cover_print.translate((-bb.xmin, -bb.ymin, -bb.zmin))
    cq.exporters.export(cover_print, "static_cover.stl", tolerance=tol, angularTolerance=ang)
    n_tri, n_bound = _stl_boundary_edges("static_cover.stl")
    print(f"static_cover.stl: {n_tri} tris, {n_bound} boundary edges")
    assert n_bound == 0, f"static_cover.stl not watertight ({n_bound} boundary edges)"
    cq.exporters.export(cover, "static_cover.step")
    print("exported static_cover.stl / .step")

    tray = as_single_solid(build_pm_tray(), "pm_tray")
    tray_print = tray.rotate((0, 0, 0), (1, 0, 0), -90)
    bb = tray_print.val().BoundingBox()
    tray_print = tray_print.translate((-bb.xmin, -bb.ymin, -bb.zmin))
    cq.exporters.export(tray_print, "pm_tray.stl", tolerance=tol, angularTolerance=ang)
    n_tri, n_bound = _stl_boundary_edges("pm_tray.stl")
    print(f"pm_tray.stl: {n_tri} tris, {n_bound} boundary edges")
    assert n_bound == 0, f"pm_tray.stl not watertight ({n_bound} boundary edges)"
    cq.exporters.export(tray, "pm_tray.step")
    print("exported pm_tray.stl / .step")

    # QC interior layout + routing (unique names per revision).
    render_right_interior_png(right)
    render_routing_png()

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
