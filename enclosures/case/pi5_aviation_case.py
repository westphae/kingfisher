#!/usr/bin/env python3
# =============================================================================
#  Raspberry Pi 5 aviation enclosure  (Stratux-style: Pi + GPS + IMU)
#  Parametric model -> STEP (editable in Fusion) + STL (print-ready)
#  Printer: AnkerMake M5C / PETG.  Fasteners: M2.5 heat-set inserts + screws
#  (bay/lid); the Pi stack mounts on its own metal standoffs (see below).
#
#  v3 changes from v2 (measured hardware: ../x1200_m2hat_stack.md):
#   - Geekworm X1200 UPS added UNDER the Pi (18650s facing down), official
#     M.2 HAT+ ABOVE it (16 mm standoffs, over the active cooler), IDC ribbon
#     connector on extended GPIO pins above the HAT (RIBBON_ON_TOP)
#   - explicit Z-stack: every deck height derives from the stack parameters;
#     wall cutouts (USB-C, I/O window) follow the Pi deck automatically
#   - stack mounts via 4x M2.5 M-F standoffs threaded into the X1200's
#     threaded bosses from below; case screws come UP through the floor into
#     the standoffs' female ends (counterbored flush -> bottom stays flat for
#     Dual-Lock). No heat-set inserts for the stack; bay/lid keep theirs.
#   - CLR_L 1.0 -> 6.0: PCIe FPC ribbon bulges ~5 mm past the HAT's left edge
#     (also clears the Pi power button and the X1200 button overhang)
#   - microSD L-access removed (NVMe boot; SD is vestigial)
#   - new front-wall cutout for the X1200 USB-C charge input, with an interior
#     pilaster so the plug seats ~flush; new left-wall X1200 power-button
#     finger recess and LED openings; left exhaust slots moved up to cooler
#     level in two groups flanking the FPC keep-out
#   - the X1200's two BACK-edge LEDs face the GPS bay interior: no wall to
#     pipe them through; the left bank carries the same status -> skipped
#
#  v2 changes from first print feedback:
#   - tighter clearances below & around (cooling proven to be fine)
#   - USB-A/Ethernet sit ~flush: I/O wall pulled in, window widened
#   - velcro pad removed (flat solid bottom for Dual-Lock)
#   - lid lip split into segments with corner breaks (cleared the screw posts)
#   - lid exported upside-down (prints with no lip support)
# =============================================================================
import cadquery as cq

# ----------------------------------------------------------------------------- PARAMETERS (mm)
WALL        = 3.0
LID_T       = 4.0       # 2.30 mm socket-cap head fully recesses with solid plastic beneath
LID_LIP_H   = 3.0       # how far the lid lip drops into the case
LID_LIP_GAP = 0.4       # lip-to-wall clearance (tune per printer)
LIP_T       = 1.6       # lip rib thickness
LIP_CORNER_GAP = 7.0    # lip stops this far from each corner (clears the posts)
SCREW_HEAD_H = 2.30     # lid bolts: M2.5 socket cap head thickness
SCREW_HEAD_D = 4.5      # M2.5 socket cap head OD (counterbore is cut a touch wider)

# clearances around the Pi/X1200/HAT footprint (they share the 85x56 outline)
CLR_L       = 6.0       # left (-X): PCIe FPC ribbon bulges FPC_BULGE past the HAT
                        # edge; also covers the Pi power button (~3 mm) and the
                        # X1200 button (2.7 mm, gets a wall recess below)
CLR_F       = 4.3       # front (-Y, USB-C edge) gap. v3: set by DROP-IN clearance —
                        # the assembled stack lowers past the floating front posts,
                        # so the board edge must clear the post OD: POST_OFF +
                        # BOSS_D/2 + 0.3 margin (the old Ethernet limit was 3.5)
IO_CLR      = 0.0       # right (+X): board edge at the inner wall plane, connectors
                        # ride in the open I/O window and end flush with the wall face
BAY_GAP     = 4.0       # gap between the stack and the GPS/IMU bay
BACK_CLR    = 6.0       # behind GPS, for the two rear posts

# --- the Pi stack (Z), floor -> lid.  All decks DERIVE from these. ----------
# Mounting (see ../x1200_m2hat_stack.md): the four corner holes serve the
# WHOLE tower (case standoff / X1200 post / Pi / HAT spacer / HAT), so each
# corner is one M2.5x30 bolt from the HAT top: HAT 1.6 + spacer 15.8 + Pi 1.6
# + X1200 barrel 6.1 = 25.1 to exit the barrel, ~4.9 into a 20 mm F-F case
# standoff below (through-threaded or >=6 mm/end). The barrel threads are
# captured mid-column, not load-bearing. M2.5x8 case screws enter the
# standoff bottoms through the counterbored floor (tips clear by ~9.4).
# (M-F standoffs and shorter bolts DON'T work: the ~6 mm barrel can't serve
# two fasteners, and 20/25 mm bolts leave 1/0 mm of usable engagement.)
PAD_H, PAD_D    = 2.0, 11.0  # raised floor pads the standoffs sit on
UPS_STANDOFF_H  = 20.0  # M-F standoff below the X1200 (18.6 works; 20 is the
                        # common size and floats the cells 1.5 mm higher)
UPS_UNDERSIDE   = 18.5  # X1200 PCB bottom -> 18650 holder/cell bottom (cells DOWN)
UPS_PCB_T       = 1.6
POGO_SPACER_H   = 4.5   # kit spacer X1200->Pi: sets pogo-pin engagement
PI_PCB_T        = 1.6
COOLER_L, COOLER_W, COOLER_H = 63.5, 42.5, 14.0
HAT_STANDOFF_H  = 16.0  # official M.2 HAT+ standoffs (clears the active cooler)
HAT_PCB_T       = 1.6
GPIO_EXT_H      = 9.0   # extended GPIO pins above the HAT
IDC_STRIP_H     = 4.0   # pin-retention strip the ribbon connector presses onto
IDC_H           = 15.4  # IDC ribbon connector height (incl. strain relief)
RIBBON_ON_TOP   = True  # option A: IDC lives on the pins above the HAT.
                        # False = pins only (discrete harness), case ~10 shorter
STACK_HEADROOM  = 2.5   # stack top -> lid underside (also absorbs the ~1 mm
                        # bench-vs-sum measurement disagreement)

# base screws: M2.5x8 pan head, up through the floor into the standoffs
BASE_HOLE_D   = 2.8
BASE_CB_D     = 5.6     # counterbore for the pan head (flush bottom, Dual-Lock)
BASE_CB_DEPTH = 2.7

# --- M2.5 brass heat-set inserts (bay pedestals + lid posts only) -----------
INSERT_OD   = 3.47      # insert outside diameter (knurled body), mm
INSERT_LEN  = 3.98      # insert length = how deep it sits when pressed flush, mm
MELT_ALLOWANCE   = 0.30 # pilot hole is OD minus this (PETG; confirm on a coupon)
HOLE_DEPTH_EXTRA = 0.50 # drill past the insert so it seats flush w/ relief room
INS_HOLE_D  = INSERT_OD  - MELT_ALLOWANCE
INS_DEPTH   = INSERT_LEN + HOLE_DEPTH_EXTRA
SCREW_RELIEF_D     = 2.9   # narrower bore continuing below the insert
SCREW_RELIEF_EXTRA = 4.0   # how far the relief reaches past the insert
SCREW_RELIEF_FLOOR = 1.0   # min plastic left at the far end

BOSS_D      = 6.0       # boss OD around an insert; lean but wall-fused (see v2)
POST_OFF    = 1.0       # lid-post centre, in from the corner
POST_LEN    = 13.0      # lid posts hang from the top this far; 45 deg taper beneath

# Raspberry Pi 5 (the X1200 and the mount-hole pattern share this outline)
PI_L, PI_W  = 85.0, 56.0
PI_HOLE_INSET, PI_HOLE_DX, PI_HOLE_DY = 3.5, 58.0, 49.0
USBC_X      = 11.2      # Pi USB-C centre from the left board edge

# I/O (+X short) edge connector layout, from the RPi5 mechanical drawing:
#   Ethernet ~1.0..19.4, USB3 ~22.4..35.8, USB2 ~40.3..53.7 (from front corner)
IO_WIN_FRONT_INSET = 2.0
IO_WIN_BACK_INSET  = 1.0
IO_WIN_H           = 18.0  # covers the ~16 mm connector stack above the PCB bottom

# X1200 front USB-C charge input (5 V / 5 A; on the PCB TOP side)
UPSC_FROM_LEFT = 52.5   # connector centre from the left board edge (=85-32.5)
UPSC_W, UPSC_H = 8.8, 3.1
UPSC_WIN_W, UPSC_WIN_H = 13.0, 7.0  # window passes the plug OVERMOLD so the
                        # plug fully seats on the recessed connector ("flush")

# X1200 left-edge power button (momentary, mirrors the Pi's; auto power-on)
BTN_FROM_BACK = 14.0    # button centre from the X1200 back edge
BTN_DROP      = 4.0     # button centre sits this far BELOW the X1200 PCB bottom
BTN_OVERHANG  = 2.7     # button protrudes past the board edge (lives in CLR_L)
BTN_HOLE_D    = 17.0    # finger hole; button ends up recessed ~6 mm from the
                        # outer face -> can't be pressed inadvertently

# X1200 left-edge status LEDs (top side): 13-wide bank centred on the face
# (25/50/75/100% + power) plus a charge LED 10.0 from the front corner
LED_SLOT_W, LED_SLOT_H = 14.0, 3.0   # slot over the bank (fill w/ clear epoxy
                                     # or press a strip light pipe)
CHG_LED_FROM_FRONT = 10.0
LED_HOLE_D  = 3.2                    # press-fit for a 3 mm light pipe

# PCIe FPC ribbon Pi->HAT: left side, centred on the edge
FPC_BULGE = 5.0         # sticks out this far past the HAT's left edge
FPC_W     = 8.6

# SparkFun NEO-M9N SMA (1.60 x 1.45 in); 4 corner holes 0.10" in.
GPS_L, GPS_W    = 40.64, 36.83
GPS_HOLE_INSET  = 2.54
GPS_PED_H       = 6.0    # GPS sits low: just tall enough to hold the inserts
SMA_ADAPTER_EXT = 18.0   # jack + right-angle adapter reach, +X off the GPS edge

# ICM-45686 IMU breakout (two insert bosses forward, two plain nubs aft)
IMU_L, IMU_W   = 20.6, 17.8
IMU_HOLE_SHORT_INSET, IMU_HOLE_LONG_INSET = 2.5, 5.0
IMU_NUB_FAR_INSET = 5.0
IMU_PED_H      = 12.0    # raised: ~12 mm of cable routing room under the board

# SMA panel-mount bulkhead on the RIGHT (+X) wall, low, in line with the GPS jack
SMA_D          = 6.5

# vents
INTAKE_R, INTAKE_HOLE_D, INTAKE_PITCH = 21.0, 4.0, 6.5
SLOT_W      = 3.0        # left-wall exhaust slot width

# ----------------------------------------------------------------------------- DERIVED
# The Z-stack, floor -> lid. PI_DECK_Z (Pi PCB top) is the master deck height.
UPS_BOT_Z    = WALL + PAD_H + UPS_STANDOFF_H
UPS_TOP_Z    = UPS_BOT_Z + UPS_PCB_T
PI_BOT_Z     = UPS_TOP_Z + POGO_SPACER_H
PI_DECK_Z    = PI_BOT_Z + PI_PCB_T
COOLER_TOP_Z = PI_DECK_Z + COOLER_H
HAT_BOT_Z    = PI_DECK_Z + HAT_STANDOFF_H
HAT_TOP_Z    = HAT_BOT_Z + HAT_PCB_T
STACK_TOP_Z  = HAT_TOP_Z + (IDC_STRIP_H + IDC_H if RIBBON_ON_TOP else GPIO_EXT_H)

INT_X = CLR_L + PI_L + IO_CLR
INT_Y = CLR_F + PI_W + BAY_GAP + GPS_W + BACK_CLR
OX, OY = 2*WALL + INT_X, 2*WALL + INT_Y
OZ     = STACK_TOP_Z + STACK_HEADROOM
WALL_H = OZ - WALL                     # interior wall height (reference)

def io(x, y): return (WALL + x, WALL + y)

PIx0, PIy0 = io(CLR_L, CLR_F)
pi_holes = [(PIx0 + PI_HOLE_INSET + dx, PIy0 + PI_HOLE_INSET + dy)
            for dx in (0, PI_HOLE_DX) for dy in (0, PI_HOLE_DY)]
cooler_cx, cooler_cy = PIx0 + PI_L/2, PIy0 + PI_W/2

lid_posts = [io(POST_OFF, POST_OFF), io(INT_X-POST_OFF, POST_OFF),
             io(POST_OFF, INT_Y-POST_OFF), io(INT_X-POST_OFF, INT_Y-POST_OFF)]

# stack guards. The assembled stack drops straight in from the top, so the
# posts only need HORIZONTAL clearance to the 85x56 outline (the HAT pokes
# 0.25 past it in Y; the FPC bulge is on the left, away from the posts).
assert PAD_H + UPS_STANDOFF_H - UPS_UNDERSIDE >= 1.0, "18650 cells touch the floor"
assert WALL + PAD_H - BASE_CB_DEPTH >= 2.0, "counterbore leaves too little floor under the standoffs"
assert STACK_HEADROOM >= 2.0, "stack too close to the lid"
assert CLR_L >= FPC_BULGE + 1.0, "FPC ribbon touches the left wall"
assert CLR_L >= BTN_OVERHANG + 1.0, "X1200 button overhang fouls the left wall"
# The right board edge is flush to the wall (IO_CLR=0), so the front-right
# post can only be cleared in Y: the stack drops in only if the board front
# edge clears the post cylinders.
assert PIy0 >= WALL + POST_OFF + BOSS_D/2 + 0.25, \
    "stack can't drop past the front lid posts (raise CLR_F)"
# front-right post vs the tall I/O connector nearest the front corner:
IO_FRONT_CONN_S = 1.0                              # near edge of that connector
FR_CONN_GAP = (PIy0 + IO_FRONT_CONN_S) - io(0, POST_OFF)[1] - BOSS_D/2
assert FR_CONN_GAP >= 0, f"front-right post fouls the I/O connector by {-FR_CONN_GAP:.2f} mm"

# X1200 wall features (left wall unless noted)
BTN_Y = PIy0 + PI_W - BTN_FROM_BACK
BTN_Z = UPS_BOT_Z - BTN_DROP
assert BTN_Z - BTN_HOLE_D/2 >= WALL + 1.0, "button finger hole cuts into the floor"
LED_CY   = PIy0 + PI_W/2                 # bank centred on the left face
LED_Z0   = UPS_TOP_Z - 0.2               # slot bottom, just below the top-side LEDs
CHG_Y    = PIy0 + CHG_LED_FROM_FRONT
CHG_CZ   = UPS_TOP_Z + 1.0
UPSC_X   = PIx0 + UPSC_FROM_LEFT
UPSC_CZ  = UPS_TOP_Z + UPSC_H/2

# left-wall exhaust slots at cooler level, two groups flanking the FPC keep-out
VENT_Z0 = PI_DECK_Z + 0.8
VENT_H  = COOLER_H - 0.8                 # slots end at the cooler top
vent_ys = [PIy0 + o for o in (5.5, 13.0, 20.5, 35.5, 43.0, 50.5)]
FPC_KEEP = (LED_CY - FPC_W/2 - 1.5, LED_CY + FPC_W/2 + 1.5)
for _vy in vent_ys:
    assert _vy + SLOT_W/2 <= FPC_KEEP[0] or _vy - SLOT_W/2 >= FPC_KEEP[1], \
        f"vent slot at y={_vy:.1f} chafes the FPC ribbon"
    assert _vy + SLOT_W/2 <= PIy0 + PI_W, "vent slot overruns the stack footprint"

GPSx0, GPSy0 = io(3, CLR_F + PI_W + BAY_GAP)
gps_holes = [(GPSx0 + hx, GPSy0 + hy)
             for hx in (GPS_HOLE_INSET, GPS_L - GPS_HOLE_INSET)
             for hy in (GPS_HOLE_INSET, GPS_W - GPS_HOLE_INSET)]

# IMU centred laterally between the GPS right edge and the right (bulkhead) wall.
IMUy0 = io(0, CLR_F + PI_W + BAY_GAP + 2)[1]
IMUx0 = ((GPSx0 + GPS_L) + (OX - WALL)) / 2 - IMU_L / 2
imu_holes = [(IMUx0 + IMU_HOLE_SHORT_INSET,        IMUy0 + IMU_HOLE_LONG_INSET),
             (IMUx0 + IMU_L - IMU_HOLE_SHORT_INSET, IMUy0 + IMU_HOLE_LONG_INSET)]
imu_nubs  = [(IMUx0 + 3,         IMUy0 + IMU_W - IMU_NUB_FAR_INSET),
             (IMUx0 + IMU_L - 3, IMUy0 + IMU_W - IMU_NUB_FAR_INSET)]

SMA_GAP = IMUx0 - (GPSx0 + GPS_L)        # GPS right edge -> IMU left edge
assert SMA_GAP >= 4.0, f"GPS-IMU gap {SMA_GAP:.1f} too small"
assert IMUx0 + IMU_L <= OX - WALL - 1.0, "IMU overruns the right wall"

# SMA bulkhead: RIGHT (+X) wall, in line with the GPS jack, low near the floor
SMA_Y = GPSy0 + GPS_W - 11.18              # jack sits 11.18 mm from the GPS back edge
SMA_Z = WALL + 5.0
assert SMA_Z - SMA_D/2 >= WALL + 1.4, "SMA hole too low: <1.4 mm wall below it"
assert SMA_Y - WALL > (IMUy0 - WALL) + IMU_W + 3, "SMA bulkhead not clear behind the IMU"

# cable hold-down clips (see v2 notes; positions still being found empirically)
INSTALL_CLIPS = False
cable_clips = [(GPSx0 + GPS_L + 11.0, SMA_Y,                'x'),
               (IMUx0 + IMU_L*0.55,   SMA_Y,                'x'),
               ((GPSx0 + GPS_L + IMUx0)/2, IMUy0 + IMU_W*0.45, 'y')]
assert SMA_Y - 3.5 > IMUy0 + IMU_W, "X cable clips would touch the IMU back edge"
_gx = (GPSx0 + GPS_L + IMUx0)/2
assert GPSx0 + GPS_L < _gx - 2.8 and _gx + 2.8 < IMUx0, "gap (Y) clip overruns GPS/IMU"

# ----------------------------------------------------------------------------- BASE
def boss(points, top_z, with_insert=True, d=BOSS_D):
    s = cq.Workplane("XY").pushPoints(points).circle(d/2).extrude(top_z)
    if with_insert:
        s = s.faces(">Z").workplane().pushPoints(points).hole(INS_HOLE_D, INS_DEPTH)
        relief = min(INS_DEPTH + SCREW_RELIEF_EXTRA, top_z - SCREW_RELIEF_FLOOR)
        if relief > INS_DEPTH:
            s = s.faces(">Z").workplane().pushPoints(points).hole(SCREW_RELIEF_D, relief)
    return s

def lid_boss(points, post_len, d=BOSS_D):
    # Lid-screw posts hanging from the top; 45-deg cone tips print support-free.
    z_cyl = OZ - post_len
    z_tip = z_cyl - d/2
    s = None
    for (px, py) in points:
        col = (cq.Workplane("XY").workplane(offset=z_tip)
               .circle(0.4).workplane(offset=d/2).circle(d/2).loft()
               .faces(">Z").workplane().circle(d/2).extrude(post_len)
               .translate((px, py, 0)))
        s = col if s is None else s.union(col)
    s = s.faces(">Z").workplane().pushPoints(points).hole(INS_HOLE_D, INS_DEPTH)
    relief = min(INS_DEPTH + SCREW_RELIEF_EXTRA, post_len - SCREW_RELIEF_FLOOR)
    if relief > INS_DEPTH:
        s = s.faces(">Z").workplane().pushPoints(points).hole(SCREW_RELIEF_D, relief)
    return s

def cable_clip(cx, cy, axis='x', slot_w=2.60, wall_t=1.5, h=3.4, length=5.0,
               nib=0.26, roof_t=0.9):
    # Snug snap clip for the 2.48 mm coax; see v2 notes.
    roof_z = h - roof_t
    g = None
    for s in (-1, 1):
        off  = s * (slot_w/2 + wall_t/2)
        noff = s * (slot_w/2 - nib/2)
        if axis == 'x':
            w  = (cq.Workplane("XY").box(length, wall_t, h, centered=(True,True,False))
                  .translate((cx, cy + off, WALL)))
            nb = (cq.Workplane("XY").box(length, nib, roof_t, centered=(True,True,False))
                  .translate((cx, cy + noff, WALL + roof_z)))
        else:
            w  = (cq.Workplane("XY").box(wall_t, length, h, centered=(True,True,False))
                  .translate((cx + off, cy, WALL)))
            nb = (cq.Workplane("XY").box(nib, length, roof_t, centered=(True,True,False))
                  .translate((cx + noff, cy, WALL + roof_z)))
        w = w.union(nb)
        g = w if g is None else g.union(w)
    try:
        g = g.faces(">Z").chamfer(0.25)
    except Exception:
        pass
    return g

def build_base():
    b = cq.Workplane("XY").box(OX, OY, OZ, centered=False).faces(">Z").shell(-WALL)

    # stack mount pads: the M-F standoffs sit on these; screws come up from
    # below through counterbored holes into the standoffs' female threads
    b = b.union(boss(pi_holes, WALL + PAD_H, with_insert=False, d=PAD_D))
    b = b.union(lid_boss(lid_posts, POST_LEN))
    b = b.union(boss(gps_holes, WALL + GPS_PED_H))
    b = b.union(boss(imu_holes, WALL + IMU_PED_H))
    b = b.union(boss(imu_nubs,  WALL + IMU_PED_H, with_insert=False, d=4.0))

    # interior pilaster behind the X1200 USB-C: brings the wall to ~0.3 mm off
    # the board edge so the plug overmold passes and the plug seats fully
    b = b.union(cq.Workplane("XY")
                .box(UPSC_WIN_W + 4, CLR_F - 0.3, UPSC_CZ + 6.0 - WALL,
                     centered=(True,True,False))
                .translate((UPSC_X, WALL + (CLR_F - 0.3)/2, WALL)))

    if INSTALL_CLIPS:
        for (cx, cy, ax) in cable_clips:
            b = b.union(cable_clip(cx, cy, ax))

    # base screw holes + counterbores (from below; bottom face stays flat)
    b = b.cut(cq.Workplane("XY").workplane(offset=-1).pushPoints(pi_holes)
              .circle(BASE_HOLE_D/2).extrude(WALL + PAD_H + 2))
    b = b.cut(cq.Workplane("XY").workplane(offset=-1).pushPoints(pi_holes)
              .circle(BASE_CB_D/2).extrude(1 + BASE_CB_DEPTH))

    # Pi USB-C cutout (front wall, at the Pi deck; kept for bench/debug use)
    b = b.cut(cq.Workplane("XY").box(13, WALL*3, 9, centered=(True,True,False))
              .translate((PIx0 + USBC_X, WALL, PI_BOT_Z - 0.5)))

    # X1200 USB-C charge window (front wall + pilaster) + lead-in mouth
    b = b.cut(cq.Workplane("XY")
              .box(UPSC_WIN_W, 2*(WALL + CLR_F), UPSC_WIN_H, centered=(True,True,False))
              .translate((UPSC_X, WALL, UPSC_CZ - UPSC_WIN_H/2)))
    b = b.cut(cq.Workplane("XY")
              .box(UPSC_WIN_W + 3, 2.4, UPSC_WIN_H + 3, centered=(True,True,False))
              .translate((UPSC_X, 0.0, UPSC_CZ - (UPSC_WIN_H + 3)/2)))

    # I/O window (right wall): Ethernet + USB stack, follows the Pi deck
    io_w  = PI_W - IO_WIN_FRONT_INSET - IO_WIN_BACK_INSET
    io_cy = PIy0 + IO_WIN_FRONT_INSET + io_w/2
    b = b.cut(cq.Workplane("XY").box(WALL*3, io_w, IO_WIN_H, centered=(True,True,False))
              .translate((OX-WALL, io_cy, PI_BOT_Z)))

    # SMA bulkhead (RIGHT wall, in line with the GPS jack)
    b = b.cut(cq.Workplane("YZ").workplane(offset=OX-WALL*1.5).center(SMA_Y, SMA_Z)
              .circle(SMA_D/2).extrude(WALL*3))

    # X1200 power button finger recess (left wall). The button tip ends up
    # ~6 mm inside the outer face: reachable, not bumpable.
    b = b.cut(cq.Workplane("YZ").workplane(offset=-1).center(BTN_Y, BTN_Z)
              .circle(BTN_HOLE_D/2).extrude(WALL + 2))

    # X1200 status LEDs (left wall): slot over the bank + charge-LED pipe hole
    b = b.cut(cq.Workplane("XY").box(WALL*3, LED_SLOT_W, LED_SLOT_H, centered=(True,True,False))
              .translate((0, LED_CY, LED_Z0)))
    b = b.cut(cq.Workplane("YZ").workplane(offset=-1).center(CHG_Y, CHG_CZ)
              .circle(LED_HOLE_D/2).extrude(WALL + 2))

    # exhaust slots (left wall) at cooler level, flanking the FPC keep-out
    slot = cq.Workplane("XY").box(WALL*3, SLOT_W, VENT_H, centered=(True,True,False))
    for y in vent_ys:
        b = b.cut(slot.translate((0, y, VENT_Z0)))
    return b

# ----------------------------------------------------------------------------- LID
def lipbar(x0, y0, dx, dy):
    return cq.Workplane("XY").box(dx, dy, LID_LIP_H, centered=False)\
             .translate((x0, y0, OZ - LID_LIP_H))

def build_lid():
    lid = cq.Workplane("XY").box(OX, OY, LID_T, centered=False).translate((0,0,OZ))

    # segmented alignment lip on all four sides, with corner breaks for the posts
    g, t = LID_LIP_GAP, LIP_T
    span_x = (WALL+LIP_CORNER_GAP, INT_X-2*LIP_CORNER_GAP)
    span_y = (WALL+LIP_CORNER_GAP, INT_Y-2*LIP_CORNER_GAP)
    lid = lid.union(lipbar(span_x[0], WALL+g,            span_x[1], t))            # front
    lid = lid.union(lipbar(span_x[0], WALL+INT_Y-g-t,    span_x[1], t))            # back
    lid = lid.union(lipbar(WALL+g,    span_y[0],         t,         span_y[1]))    # left
    lid = lid.union(lipbar(WALL+INT_X-g-t, span_y[0],    t,         span_y[1]))    # right (I/O)

    # intake grille over the cooler (air path: lid -> around the HAT -> fan)
    pts, n = [], int(INTAKE_R/INTAKE_PITCH)+1
    for i in range(-n, n+1):
        for j in range(-n, n+1):
            px, py = i*INTAKE_PITCH, j*INTAKE_PITCH
            if px*px+py*py <= (INTAKE_R-INTAKE_HOLE_D/2)**2:
                pts.append((cooler_cx+px, cooler_cy+py))
    lid = lid.cut(cq.Workplane("XY").pushPoints(pts).circle(INTAKE_HOLE_D/2)
                  .extrude(LID_T+1).translate((0,0,OZ-0.5)))

    # lid screw holes: 2.8 clearance through, + counterbore for the socket cap
    cb_depth = SCREW_HEAD_H + 0.3
    cb_r     = (SCREW_HEAD_D + 0.5) / 2
    lid = lid.cut(cq.Workplane("XY").pushPoints(lid_posts).circle(1.4)
                  .extrude(LID_T+1).translate((0,0,OZ-0.5)))
    lid = lid.cut(cq.Workplane("XY").pushPoints(lid_posts).circle(cb_r)
                  .extrude(cb_depth+0.1).translate((0,0,OZ+LID_T-cb_depth)))

    # rear exhaust slots over the bay
    for i in range(5):
        lid = lid.cut(cq.Workplane("XY").box(3, 22, LID_T+1, centered=True)
                      .translate((OX/2 + (i-2)*9, GPSy0+GPS_W/2, OZ+LID_T/2)))
    return lid

# ----------------------------------------------------------------------------- BUILD + EXPORT
if __name__ == "__main__":
    base = build_base()
    lid  = build_lid()

    # lid for printing: flip 180 deg about X so the flat top is on the bed
    lid_print = lid.rotate((0,0,0),(1,0,0),180)
    bb = lid_print.val().BoundingBox()
    lid_print = lid_print.translate((-bb.xmin, -bb.ymin, -bb.zmin))

    cq.exporters.export(base, "case_base.step")
    cq.exporters.export(base, "case_base.stl", tolerance=0.05)
    cq.exporters.export(lid_print, "case_lid.step")
    cq.exporters.export(lid_print, "case_lid.stl", tolerance=0.05)
    cq.exporters.export(lid, "_lid_asm.stl", tolerance=0.05)   # assembled, render only

    asm = cq.Assembly()
    asm.add(base, name="base", color=cq.Color(0.30,0.45,0.65))
    asm.add(lid,  name="lid",  color=cq.Color(0.75,0.78,0.82))
    asm.save("case_assembly.step")

    print(f"Outer footprint : {OX:.1f} x {OY:.1f} x {OZ+LID_T:.1f} mm")
    print(f"Z-stack: X1200 bottom {UPS_BOT_Z:.1f} | Pi deck {PI_DECK_Z:.1f} | "
          f"cooler top {COOLER_TOP_Z:.1f} | HAT top {HAT_TOP_Z:.1f} | "
          f"stack top {STACK_TOP_Z:.1f} | lid underside {OZ:.1f}")
    print(f"Cell-to-floor clearance: {PAD_H + UPS_STANDOFF_H - UPS_UNDERSIDE:.1f} mm")
    print("Exported base/lid .step/.stl (+ assembly). Lid STL is print-oriented (flipped).")
