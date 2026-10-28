#!/usr/bin/env python3
# =============================================================================
#  Raspberry Pi 5 aviation enclosure  (Stratux-style: Pi + GPS + IMU)
#  Parametric model -> STEP (editable in Fusion) + STL (print-ready)
#  Printer: AnkerMake M5C / PETG.  Fasteners: M2.5 heat-set inserts + screws
#  (bay/lid); the Pi stack mounts on its own metal standoffs (see below).
#
#  v4 (tray + hood):
#   - base is a plate: stack pads, bay pedestals, and a short right bulkhead
#     under the Ethernet/USB shells (SMA bulkhead in that same wall)
#   - bulkhead runs from the Ethernet front face to the same inset at the rear
#   - hood carries the lid, the front/back/left walls, right corner legs, and
#     the band above the ports. Pi USB-C is closed over (charge the X1200 only)
#   - hood screws: four M2.5x10 countersunk from the bottom into short corner
#     bosses (inserts) on the hood. All four bosses are backed by full-height walls
#   - right-wall lip is a half-lap on the three free edges of the bulkhead
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
LID_T       = 4.0
# tray/hood joint. Groove is in the plate; skirts on the hood drop into it.
GROOVE_D    = 1.2       # depth into the 3 mm plate
GROOVE_SIDE = 0.2       # skirt steps in from the outer face
GROOVE_IN   = 0.4       # groove continues this far past the inner face
CORNER_SKIP = 8.0       # groove stays clear of the corner countersinks
HOOD_BOSS_H = 10.0      # corner-boss height above the plate (insert from below)
LAP_T       = 1.5       # half-wall lip thickness
LAP_Z       = 2.0       # top-lip engagement, aft of the USB sockets
LAP_CLEAR   = 0.3       # fit gap on the bulkhead lip
LAP_X_GAP   = 0.15      # thickness-direction gap so the two tongues don't bind
CSK_D       = 5.2       # M2.5 90° countersunk head, slightly oversize

# clearances around the Pi/X1200/HAT footprint (they share the 85x56 outline)
CLR_L       = 6.0       # left (-X): PCIe FPC ribbon bulges FPC_BULGE past the HAT
                        # edge; also covers the Pi power button (~3 mm) and the
                        # X1200 button (2.7 mm, gets a wall recess below)
CLR_F       = 4.3       # front (-Y) gap. Set so the hood's front corner bosses clear
                        # the board edge: POST_OFF + BOSS_D/2 + 0.3 (the bosses
                        # slide down those corners when the hood goes on)
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

# base screws: M2.5x8 pan head, up through the floor into the standoffs.
# Hood screws are M2.5x10 countersunk through the same clearance diameter.
BASE_HOLE_D   = 2.8
BASE_CB_D     = 5.6     # counterbore for the stack pan head (flush bottom, Dual-Lock)
BASE_CB_DEPTH = 2.7
CSK_H         = (CSK_D - BASE_HOLE_D) / 2   # 90° cone for the hood screws

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
POST_OFF    = 1.0       # hood corner-boss centre, in from the corner

# Raspberry Pi 5 (the X1200 and the mount-hole pattern share this outline)
PI_L, PI_W  = 85.0, 56.0
PI_HOLE_INSET, PI_HOLE_DX, PI_HOLE_DY = 3.5, 58.0, 49.0
USBC_X      = 11.2      # Pi USB-C centre from the left board edge

# I/O (+X short) edge connector layout, from the RPi5 mechanical drawing:
#   Ethernet ~1.0..19.4, USB3 ~22.4..35.8, USB2 ~40.3..53.7 (from front corner)
ETH_FROM_PI        = 1.0
USB2_BACK_FROM_PI  = 53.7
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

# SparkFun Qwiic OLED 1.3" LCD-23453 (SSD1306), lid-rear over the GPS bay.
# Drawing (inches): 1.40 body width, 1.00 between the route-outs, ears on the
# short edges, hole centres 1.20 × 1.25. Eagle layer 20: X 0..35.56, ear tips
# Y −5.715..31.115. Glass is the bottom of the PCB (package faces bottom);
# the Qwiic jacks are on the other face. Glass-up puts the jacks down in the
# GPS bay, so the cable is plugged from inside before the hood goes on.
# Measured PCB+glass is 4.52 mm. FR4 thickness is assumed 1.6 mm (the board
# file has none). The pocket seats that FR4 so the glass is flush with the
# lid top. The outer face is flat (no visor): that face is the bed contact.
# See OLED IDs in enclosures/pi5_aviation_case.md.
OLED_PANEL_X, OLED_PANEL_Y = 34.5, 23.0       # datasheet panel
OLED_ACTIVE_X, OLED_ACTIVE_Y = 29.42, 14.7
OLED_GLASS_X, OLED_GLASS_Y = 34.54, 24.13      # measured glass + bezel
OLED_WIN_FIT = 1.0                            # printed opening over the glass (O1)
OLED_WIN_X, OLED_WIN_Y = OLED_GLASS_X + OLED_WIN_FIT, OLED_GLASS_Y + OLED_WIN_FIT
OLED_PCB_X, OLED_PCB_Y = 35.56, 36.83         # body width × ear-tip span (O2)
OLED_BODY_Y = 25.40                           # 1.00 in between the route-outs
OLED_CLR = 0.4                               # pocket vs ear outline
OLED_STACK = 4.52                            # measured PCB + glass
OLED_PCB_T = 1.6                             # assumed FR4; the ears are this thick
OLED_POCKET_D = OLED_PCB_T - (OLED_STACK - LID_T)  # seats the ears; glass flush
OLED_BTN_D = 7.2                             # tactile cap clearance
OLED_BTN_OFF_X = 8.0                         # PCB +X edge → button centre
OLED_FLANK_GAP = 18.0                        # PCB half-width → flanking exhaust centre
# Ears are drilled for a #4 screw (3.30 mm). M2.5 matches the rest of the
# case; Ø3.4 is clearance and the ear hole is the fit. Nut under the ear.
OLED_BOLT_DX, OLED_BOLT_DY = 15.24, 15.875   # hole centres from the board centre
OLED_BOLT_D = 3.4
OLED_CSK_H = (CSK_D - OLED_BOLT_D) / 2.0     # 90° head, same CSK_D as the hood

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
# Hood-on: the front corner bosses slide down in front of the board. The
# board edge has to clear the boss OD (this is what pins CLR_F).
assert PIy0 >= WALL + POST_OFF + BOSS_D/2 + 0.25, \
    "hood corner bosses foul the board front (raise CLR_F)"
# front-right boss vs the Ethernet face. The corner leg ends at that face,
# so this gap is also the plastic left for the vertical lip.
FR_CONN_GAP = (PIy0 + ETH_FROM_PI) - io(0, POST_OFF)[1] - BOSS_D/2
assert FR_CONN_GAP >= 1.0, f"front-right boss leaves {FR_CONN_GAP:.2f} mm before the Ethernet"

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

# OLED on the lid, glass up, centred over the GPS bay (not the cooler).
# The opening is the measured glass plus OLED_WIN_FIT, split equally each side.
oled_cx, oled_cy = OX / 2.0, GPSy0 + GPS_W / 2.0
oled_win_x, oled_win_y = OLED_WIN_X, OLED_WIN_Y
oled_pocket_x = OLED_PCB_X + 2 * OLED_CLR
oled_pocket_y = OLED_PCB_Y + 2 * OLED_CLR
oled_btn_x = oled_cx + OLED_PCB_X / 2.0 + OLED_BTN_OFF_X
oled_btn_y = oled_cy
oled_bolts = [(oled_cx + sx * OLED_BOLT_DX, oled_cy + sy * OLED_BOLT_DY)
              for sx in (1.0, -1.0) for sy in (1.0, -1.0)]
_dx, _dy = oled_cx - cooler_cx, oled_cy - cooler_cy
assert (_dx * _dx + _dy * _dy) ** 0.5 > INTAKE_R + oled_win_y / 2.0 + 3.0, \
    "O5 OLED window overlaps cooler intake grille"
assert OLED_PCB_X - OLED_WIN_X >= 0.0, "O1 opening cuts past the PCB width"
assert OLED_BODY_Y - OLED_WIN_Y >= 0.0, "O1 opening cuts past the PCB body in Y"
assert OLED_WIN_X >= OLED_PANEL_X and OLED_WIN_Y >= OLED_PANEL_Y, \
    "O1 opening is smaller than the datasheet panel"
_ear_gap = OLED_BOLT_DY - OLED_WIN_Y / 2.0 - CSK_D / 2.0
assert _ear_gap >= 0.5, f"O1 countersink breaks into the window ({_ear_gap:.2f} mm left)"
assert OLED_BOLT_DX + OLED_BOLT_D / 2.0 + 0.4 < oled_pocket_x / 2.0, \
    "O8 bolt leaves the pocket in X"
assert OLED_BOLT_DY + OLED_BOLT_D / 2.0 + 0.4 < oled_pocket_y / 2.0, \
    "O8 bolt leaves the pocket in Y"
assert abs((OLED_STACK - OLED_PCB_T) - (LID_T - OLED_POCKET_D)) < 1e-6, \
    "O2 glass is not flush with the lid top"
assert oled_cx - oled_pocket_x / 2.0 > WALL + 2.0, "O2 OLED pocket hits left wall"
assert oled_cx + oled_pocket_x / 2.0 < OX - WALL - 2.0, "O2 OLED pocket hits right wall"
assert oled_cy - oled_pocket_y / 2.0 > PIy0 + PI_W + 1.0, "O2 OLED pocket overlaps stack"
assert oled_cy + oled_pocket_y / 2.0 < OY - WALL - 2.0, "O2 OLED pocket hits back wall"
assert LID_T - OLED_POCKET_D >= 1.0, "O2 OLED pocket leaves <1 mm lid lip"
assert oled_btn_x + OLED_BTN_D / 2.0 < OX - WALL - 1.0, "O4 OLED button overruns right wall"
for sx in (oled_cx - OLED_PCB_X / 2.0 - OLED_FLANK_GAP,
           oled_cx + OLED_PCB_X / 2.0 + OLED_FLANK_GAP):
    assert abs(sx - oled_btn_x) > OLED_BTN_D / 2.0 + SLOT_W / 2.0 + 2.0, \
        "O6 OLED flanking vent overlaps the button"
for _px, _py in lid_posts:
    _d = ((_px - oled_btn_x) ** 2 + (_py - oled_btn_y) ** 2) ** 0.5
    assert _d > BOSS_D / 2.0 + OLED_BTN_D / 2.0 + 2.0, \
        f"O4 OLED button fouls lid post at ({_px:.1f},{_py:.1f})"
    _d = ((_px - oled_cx) ** 2 + (_py - oled_cy) ** 2) ** 0.5
    assert _d > BOSS_D / 2.0 + max(oled_pocket_x, oled_pocket_y) / 2.0, \
        f"O2 OLED pocket fouls lid post at ({_px:.1f},{_py:.1f})"


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

# Right bulkhead on the plate: Ethernet front face, same inset at the rear,
# top at the Pi underside (the sill under the port shells).
BULK_FRONT = PIy0 + ETH_FROM_PI
BULK_BACK  = OY - BULK_FRONT
BOSS_REACH = WALL + POST_OFF + BOSS_D/2          # boss OD, measured from the outer face
USB_BACK   = PIy0 + USB2_BACK_FROM_PI
PORT_TOP   = PI_BOT_Z + IO_WIN_H
AFT_WALL_Y = USB_BACK + LAP_CLEAR                # hood wall comes down to the sill here
assert BULK_FRONT - BOSS_REACH >= 1.0, "no room for the corner leg behind the boss"
# tongues: base inner tongue starts just past the boss; hood outer tongue
# stops just short of the Ethernet face. They overlap in Y, split in X.
LAP_BASE_Y0 = BOSS_REACH + LAP_CLEAR
HOOD_TONGUE_Y = BULK_FRONT - LAP_CLEAR
assert HOOD_TONGUE_Y - LAP_BASE_Y0 >= 0.5, \
    f"vertical lip engagement {HOOD_TONGUE_Y - LAP_BASE_Y0:.2f} mm < 0.5"
assert BULK_BACK - (SMA_Y + SMA_D/2) >= 6.0, "SMA nut runs off the rear of the bulkhead"
assert AFT_WALL_Y + 10 < BULK_BACK, "top lip aft of the USB sockets is too short"
assert BULK_FRONT < USB_BACK - 20, "bulkhead does not carry the port stack"

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

def slab(x0, y0, z0, x1, y1, z1):
    """Axis-aligned box. Callers overlap neighbours by ~0.2 mm so the fuse is a volume."""
    return (cq.Workplane("XY")
            .box(x1 - x0, y1 - y0, z1 - z0, centered=False)
            .translate((x0, y0, z0)))

def _u(a, b):
    return b if a is None else a.union(b)

# Right-wall lip, split through the thickness. Inner tongue stays on the
# plate; outer tongue is on the hood and hides the seam.
X_IN, X_OUT = OX - WALL, OX
X_SPLIT = OX - LAP_T
X_BASE_T = X_SPLIT - LAP_X_GAP
X_HOOD_T = X_SPLIT + LAP_X_GAP

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
    # Plate. The other three walls live on the hood.
    b = slab(0, 0, 0, OX, OY, WALL)

    # Perimeter groove the hood skirts drop into. Stopped short of the
    # corners so it doesn't break into the countersinks.
    b = b.cut(slab(0, CORNER_SKIP, WALL - GROOVE_D,
                   WALL + GROOVE_IN, OY - CORNER_SKIP, WALL + 0.2))
    b = b.cut(slab(CORNER_SKIP, 0, WALL - GROOVE_D,
                   OX - CORNER_SKIP, WALL + GROOVE_IN, WALL + 0.2))
    b = b.cut(slab(CORNER_SKIP, OY - (WALL + GROOVE_IN), WALL - GROOVE_D,
                   OX - CORNER_SKIP, OY, WALL + 0.2))

    # Right bulkhead: sill under the Ethernet/USB shells, SMA further aft.
    b = _u(b, slab(X_IN, BULK_FRONT, 0, X_OUT, BULK_BACK, PI_BOT_Z))
    # Inner tongues reach toward the corner bosses. The hood's outer tongue
    # slides down beside them.
    b = _u(b, slab(X_IN, LAP_BASE_Y0, 0, X_BASE_T, BULK_FRONT + 0.2, PI_BOT_Z))
    b = _u(b, slab(X_IN, BULK_BACK - 0.2, 0, X_BASE_T, OY - LAP_BASE_Y0, PI_BOT_Z))
    # Top lip aft of the USB sockets: outer half steps down, hood tongue fills it.
    b = b.cut(slab(X_SPLIT, AFT_WALL_Y, PI_BOT_Z - LAP_Z,
                   X_OUT, BULK_BACK, PI_BOT_Z + 0.5))

    b = _u(b, boss(pi_holes, WALL + PAD_H, with_insert=False, d=PAD_D))
    b = _u(b, boss(gps_holes, WALL + GPS_PED_H))
    b = _u(b, boss(imu_holes, WALL + IMU_PED_H))
    b = _u(b, boss(imu_nubs,  WALL + IMU_PED_H, with_insert=False, d=4.0))

    if INSTALL_CLIPS:
        for (cx, cy, ax) in cable_clips:
            b = _u(b, cable_clip(cx, cy, ax))

    # Stack screws, pan heads, counterbored flush for Dual-Lock.
    b = b.cut(cq.Workplane("XY").workplane(offset=-1).pushPoints(pi_holes)
              .circle(BASE_HOLE_D/2).extrude(WALL + PAD_H + 2))
    b = b.cut(cq.Workplane("XY").workplane(offset=-1).pushPoints(pi_holes)
              .circle(BASE_CB_D/2).extrude(1 + BASE_CB_DEPTH))

    # Hood screws: M2.5x10 countersunk, up into the corner bosses.
    b = b.cut(cq.Workplane("XY").workplane(offset=-0.1).pushPoints(lid_posts)
              .circle(BASE_HOLE_D/2).extrude(WALL + 0.2))
    for px, py in lid_posts:
        cone = cq.Solid.makeCone(CSK_D/2, BASE_HOLE_D/2, CSK_H + 0.05,
                                 cq.Vector(px, py, -0.05), cq.Vector(0, 0, 1))
        b = b.cut(cq.Workplane("XY").newObject([cone]))

    # SMA bulkhead, in line with the GPS jack.
    b = b.cut(cq.Workplane("YZ").workplane(offset=OX - WALL*1.5).center(SMA_Y, SMA_Z)
              .circle(SMA_D/2).extrude(WALL*3))
    return b

# ----------------------------------------------------------------------------- HOOD (exported as the lid)
def _corner_bosses():
    """Short insert bosses, open at the bottom. The screw comes up from the plate."""
    s = None
    for px, py in lid_posts:
        s = _u(s, cq.Workplane("XY").workplane(offset=WALL).center(px, py)
                 .circle(BOSS_D/2).extrude(HOOD_BOSS_H))
    s = s.cut(cq.Workplane("XY").workplane(offset=WALL - 0.1).pushPoints(lid_posts)
              .circle(INS_HOLE_D/2).extrude(INS_DEPTH + 0.1))
    relief = min(INS_DEPTH + SCREW_RELIEF_EXTRA, HOOD_BOSS_H - SCREW_RELIEF_FLOOR)
    if relief > INS_DEPTH:
        s = s.cut(cq.Workplane("XY").workplane(offset=WALL - 0.1).pushPoints(lid_posts)
                  .circle(SCREW_RELIEF_D/2).extrude(relief + 0.1))
    return s

def _wall_skirts():
    """Necks that drop into the plate groove. Corners are skipped with the groove."""
    z0, z1 = WALL - GROOVE_D + 0.2, WALL + 0.2
    s = slab(GROOVE_SIDE, CORNER_SKIP + 0.2, z0, WALL, OY - CORNER_SKIP - 0.2, z1)
    s = _u(s, slab(CORNER_SKIP + 0.2, GROOVE_SIDE, z0,
                   OX - CORNER_SKIP - 0.2, WALL, z1))
    s = _u(s, slab(CORNER_SKIP + 0.2, OY - WALL, z0,
                   OX - CORNER_SKIP - 0.2, OY - GROOVE_SIDE, z1))
    return s

def _right_side():
    """Corner legs full height, a band over the ports, and the sill lip aft of USB2."""
    z_top = OZ + 0.2
    y_rear_tongue = OY - HOOD_TONGUE_Y
    y_rear_boss = OY - BOSS_REACH
    s = slab(X_IN, 0, WALL, X_OUT, BOSS_REACH, z_top)                  # front leg
    s = _u(s, slab(X_HOOD_T, BOSS_REACH - 0.2, WALL, X_OUT, HOOD_TONGUE_Y, z_top))
    s = _u(s, slab(X_IN, y_rear_boss, WALL, X_OUT, OY, z_top))          # rear leg
    s = _u(s, slab(X_HOOD_T, y_rear_tongue, WALL, X_OUT, y_rear_boss + 0.2, z_top))
    # Band above the shells, tying the two legs together.
    s = _u(s, slab(X_IN, HOOD_TONGUE_Y - 0.2, PORT_TOP, X_OUT, y_rear_tongue + 0.2, z_top))
    # Aft of USB2 the wall comes down onto the bulkhead lip.
    z_outer = PI_BOT_Z - LAP_Z + LAP_CLEAR
    z_inner = PI_BOT_Z + LAP_CLEAR
    s = _u(s, slab(X_IN, AFT_WALL_Y, z_inner, X_BASE_T, y_rear_tongue + 0.2, PORT_TOP + 0.2))
    s = _u(s, slab(X_HOOD_T, AFT_WALL_Y, z_outer, X_OUT, y_rear_tongue + 0.2, PORT_TOP + 0.2))
    return s

def _charge_port(hood):
    """X1200 USB-C only. The Pi USB-C stays closed: that port back-powers the Pi."""
    y_face = WALL + CLR_F - 0.3
    pil_y0 = WALL - 0.4
    # v3 grew this block from the floor because it was part of the base and
    # fused with the standoff pads. On the hood it has to clear those pads
    # (front-right pad reaches x≈65, y≈5.3, z=5).
    pil_z0 = WALL + PAD_H + 0.4
    hood = _u(hood, slab(UPSC_X - (UPSC_WIN_W + 4)/2, pil_y0, pil_z0,
                         UPSC_X + (UPSC_WIN_W + 4)/2, y_face, UPSC_CZ + 6.0))
    z0 = UPSC_CZ - UPSC_WIN_H/2
    hood = hood.cut(cq.Workplane("XY")
                    .box(UPSC_WIN_W, 2*(WALL + CLR_F), UPSC_WIN_H, centered=(True, True, False))
                    .translate((UPSC_X, WALL, z0)))
    # Outer lead-in, same as the v3 mouth.
    hood = hood.cut(cq.Workplane("XY")
                    .box(UPSC_WIN_W + 3, 2.4, UPSC_WIN_H + 3, centered=(True, True, False))
                    .translate((UPSC_X, 0.0, UPSC_CZ - (UPSC_WIN_H + 3)/2)))
    # Sill chamfer so the frame can slide down past the jack.
    x0 = UPSC_X - (UPSC_WIN_W + 6)/2
    cham = (cq.Workplane("YZ").workplane(offset=x0)
            .moveTo(y_face, z0).lineTo(y_face - 1.6, z0).lineTo(y_face, z0 - 1.6)
            .close().extrude(UPSC_WIN_W + 6))
    return hood.cut(cham)

def build_lid():
    z_top = OZ + 0.2
    lid = slab(0, 0, OZ, OX, OY, OZ + LID_T)
    lid = _u(lid, slab(0, 0, WALL, WALL, OY, z_top))            # left
    lid = _u(lid, slab(0, 0, WALL, OX, WALL, z_top))            # front
    lid = _u(lid, slab(0, OY - WALL, WALL, OX, OY, z_top))      # back
    lid = _u(lid, _right_side())
    lid = _u(lid, _wall_skirts())
    lid = _u(lid, _corner_bosses())
    lid = _charge_port(lid)

    # Power button, LEDs, cooler exhaust — left wall, same places as v3.
    lid = lid.cut(cq.Workplane("YZ").workplane(offset=-1).center(BTN_Y, BTN_Z)
                  .circle(BTN_HOLE_D/2).extrude(WALL + 2))
    lid = lid.cut(cq.Workplane("XY").box(WALL*3, LED_SLOT_W, LED_SLOT_H, centered=(True, True, False))
                  .translate((0, LED_CY, LED_Z0)))
    lid = lid.cut(cq.Workplane("YZ").workplane(offset=-1).center(CHG_Y, CHG_CZ)
                  .circle(LED_HOLE_D/2).extrude(WALL + 2))
    slot = cq.Workplane("XY").box(WALL*3, SLOT_W, VENT_H, centered=(True, True, False))
    for y in vent_ys:
        lid = lid.cut(slot.translate((0, y, VENT_Z0)))

    # intake grille over the cooler (air path: lid -> around the HAT -> fan)
    pts, n = [], int(INTAKE_R/INTAKE_PITCH)+1
    for i in range(-n, n+1):
        for j in range(-n, n+1):
            px, py = i*INTAKE_PITCH, j*INTAKE_PITCH
            if px*px+py*py <= (INTAKE_R-INTAKE_HOLE_D/2)**2:
                pts.append((cooler_cx+px, cooler_cy+py))
    lid = lid.cut(cq.Workplane("XY").pushPoints(pts).circle(INTAKE_HOLE_D/2)
                  .extrude(LID_T+1).translate((0,0,OZ-0.5)))

    # Viewing aperture through the lid (measured glass + 1 mm). The outer
    # face stays flat so it can sit on the bed.
    lid = lid.cut(cq.Workplane("XY").box(oled_win_x, oled_win_y, LID_T + 2, centered=True)
                  .translate((oled_cx, oled_cy, OZ + LID_T / 2.0)))

    # Ear pocket from the underside. Depth seats a 1.6 mm FR4 so the glass
    # is flush with the lid top. Rectangular: the route-outs have no FR4,
    # and this cut does not pierce the lid.
    lid = lid.cut(cq.Workplane("XY").box(oled_pocket_x, oled_pocket_y, OLED_POCKET_D + 0.2, centered=True)
                  .translate((oled_cx, oled_cy, OZ + OLED_POCKET_D / 2.0 - 0.1)))

    # M2.5 countersunk through-bolts, head on the lid top, nut under the ear.
    lid = lid.cut(cq.Workplane("XY").pushPoints(oled_bolts)
                  .circle(OLED_BOLT_D / 2.0)
                  .extrude(LID_T + 2).translate((0, 0, OZ - 0.5)))
    for bx, by in oled_bolts:
        cone = cq.Solid.makeCone(CSK_D / 2.0, OLED_BOLT_D / 2.0, OLED_CSK_H + 0.05,
                                 cq.Vector(bx, by, OZ + LID_T + 0.05),
                                 cq.Vector(0, 0, -1))
        lid = lid.cut(cq.Workplane("XY").newObject([cone]))

    # Tactile button, +X of the PCB (toward the IMU).
    lid = lid.cut(cq.Workplane("XY").pushPoints([(oled_btn_x, oled_btn_y)])
                  .circle(OLED_BTN_D / 2.0)
                  .extrude(LID_T + 2).translate((0, 0, OZ - 0.5)))

    # flanking exhaust slots left/right of the OLED (bay still vents)
    for sx in (oled_cx - OLED_PCB_X / 2.0 - OLED_FLANK_GAP,
               oled_cx + OLED_PCB_X / 2.0 + OLED_FLANK_GAP):
        lid = lid.cut(cq.Workplane("XY").box(SLOT_W, 22.0, LID_T + 1, centered=True)
                      .translate((sx, oled_cy, OZ + LID_T / 2.0)))
    return lid

# ----------------------------------------------------------------------------- QC PNG (top view, unique per OLED lid revision)
def _png_rgb(path, rows):
    import struct, zlib
    h, w = len(rows), len(rows[0])
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)
    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in rows)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

def write_lid_qc_png(path):
    """Top view of the v4 tray/hood split. A new filename per shape revision."""
    scale = 4  # px / mm
    w, h = int(OX * scale) + 1, int(OY * scale) + 1
    bg, wall, cut, win, btn = (40, 44, 52), (180, 186, 196), (20, 22, 26), (220, 230, 255), (240, 180, 80)
    recess, bolt = (96, 104, 116), (200, 90, 70)
    bulk = (70, 120, 150)
    rows = [[bg] * w for _ in range(h)]

    def fill_rect(x0, y0, x1, y1, color):
        ix0, iy0 = max(0, int(x0 * scale)), max(0, int(y0 * scale))
        ix1, iy1 = min(w - 1, int(x1 * scale)), min(h - 1, int(y1 * scale))
        for y in range(iy0, iy1 + 1):
            row = rows[h - 1 - y]  # Y-up
            for x in range(ix0, ix1 + 1):
                row[x] = color

    def fill_circle(cx, cy, r, color):
        ir = int(r * scale) + 1
        icx, icy = int(cx * scale), int(cy * scale)
        r2 = (r * scale) ** 2
        for y in range(max(0, icy - ir), min(h, icy + ir + 1)):
            row = rows[h - 1 - y]
            for x in range(max(0, icx - ir), min(w, icx + ir + 1)):
                if (x - icx) ** 2 + (y - icy) ** 2 <= r2:
                    row[x] = color

    fill_rect(0, 0, OX, OY, wall)
    fill_rect(X_IN, BULK_FRONT, X_OUT, BULK_BACK, bulk)
    fill_rect(X_IN, HOOD_TONGUE_Y, X_OUT, AFT_WALL_Y, cut)   # port mouth
    fill_rect(UPSC_X - UPSC_WIN_W/2, 0, UPSC_X + UPSC_WIN_W/2, WALL, cut)
    n = int(INTAKE_R / INTAKE_PITCH) + 1
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            px, py = i * INTAKE_PITCH, j * INTAKE_PITCH
            if px * px + py * py <= (INTAKE_R - INTAKE_HOLE_D / 2) ** 2:
                fill_circle(cooler_cx + px, cooler_cy + py, INTAKE_HOLE_D / 2, cut)
    fill_rect(oled_cx - oled_pocket_x / 2, oled_cy - oled_pocket_y / 2,
              oled_cx + oled_pocket_x / 2, oled_cy + oled_pocket_y / 2, recess)
    fill_rect(oled_cx - oled_win_x / 2, oled_cy - oled_win_y / 2,
              oled_cx + oled_win_x / 2, oled_cy + oled_win_y / 2, win)
    for bx, by in oled_bolts:
        fill_circle(bx, by, CSK_D / 2, bolt)
        fill_circle(bx, by, OLED_BOLT_D / 2, cut)
    fill_circle(oled_btn_x, oled_btn_y, OLED_BTN_D / 2, btn)
    for sx in (oled_cx - OLED_PCB_X / 2.0 - OLED_FLANK_GAP,
               oled_cx + OLED_PCB_X / 2.0 + OLED_FLANK_GAP):
        fill_rect(sx - SLOT_W / 2, oled_cy - 11, sx + SLOT_W / 2, oled_cy + 11, cut)
    for px, py in lid_posts:
        fill_circle(px, py, BOSS_D/2, btn)
        fill_circle(px, py, BASE_HOLE_D/2, cut)
    fill_circle(OX - WALL/2, SMA_Y, SMA_D/2, cut)
    _png_rgb(path, rows)

def _inside(shape, x, y, z):
    return bool(shape.isInside((x, y, z)))

def audit(base, hood):
    """H1–H8. Fail with the requirement id before any export."""
    assert len(base.solids().vals()) == 1, "base is not a single solid"
    assert len(hood.solids().vals()) == 1, "hood is not a single solid"
    bs, hs = base.val(), hood.val()

    def need(shape, p, want, msg):
        got = _inside(shape, *p)
        assert got is want, f"{msg}: inside={got} at {p}"

    # OLED lid. Glass opening, ear pocket, no cable slot, bolts, flat face.
    z_mid = OZ + LID_T / 2.0
    z_lip = OZ + LID_T - 0.25
    z_pocket = OZ + OLED_POCKET_D / 2.0
    z_above = OZ + LID_T + 0.4
    need(hs, (oled_cx, oled_cy, z_mid), False, "O1 window is closed")
    need(hs, (oled_cx, oled_cy, z_above), False, "O1 window is covered above the lid")
    y_land = oled_cy + OLED_BOLT_DY - (CSK_D / 2.0 + 0.55)
    need(hs, (oled_cx + OLED_BOLT_DX, y_land, z_lip), True,
         "O1 countersink breaks the window land")
    bx, by = oled_bolts[0]
    need(hs, (bx, by, z_mid), False, "O8 ear bolt hole is closed")
    need(hs, (bx, by, z_lip), False, "O8 ear countersink is closed")
    need(hs, (oled_cx, oled_cy + OLED_PCB_Y / 2.0 - 0.4, z_pocket), False,
         "O2 ear pocket is missing")
    need(hs, (oled_cx, oled_cy + OLED_BOLT_DY, z_lip), True,
         "O3 route-out is a through slot")
    need(hs, (oled_cx, oled_cy - oled_pocket_y / 2.0 - 1.5, z_mid), True,
         "O3 cable slot is still open")
    need(hs, (oled_btn_x, oled_btn_y, z_mid), False, "O4 button hole is closed")
    for sx in (oled_cx - OLED_PCB_X / 2.0 - OLED_FLANK_GAP,
               oled_cx + OLED_PCB_X / 2.0 + OLED_FLANK_GAP):
        need(hs, (sx, oled_cy, z_mid), False, "O6 flanking vent is closed")
    deck_xy = (oled_cx, oled_cy + oled_win_y / 2.0 + 1.2)
    need(hs, (*deck_xy, z_above), False, "O7 screen frame still stands above the lid")
    need(hs, (*deck_xy, OZ + LID_T - 0.3), True, "O7 lid deck is missing beside the window")
    need(hs, (OX / 2.0, WALL + 8.0, z_above), False, "O7 something stands above the lid face")

    # y=35 sits between the exhaust slots and clear of the button hole.
    need(bs, (WALL/2, 35, 20), False, "H1 plate grew a left wall")
    need(hs, (WALL/2, 35, 20), True, "H1 hood is missing the left wall")
    need(hs, (PIx0 + USBC_X, WALL/2, PI_BOT_Z + 4), True, "H2 Pi USB-C is open")
    need(hs, (UPSC_X, WALL/2, UPSC_CZ), False, "H3 X1200 charge port is closed")
    # Front-right standoff pad occupies this point. The charge-port frame must not.
    need(hs, (68.0, 6.2, 4.0), False, "H8 charge-port frame hits a standoff pad")
    y_port = (BULK_FRONT + USB_BACK) / 2
    need(bs, (OX - WALL/2, y_port, PI_BOT_Z - 2), True, "H4 no sill under the ports")
    need(bs, (OX - WALL/2, y_port, PI_BOT_Z + 4), False, "H4 sill rises into the ports")
    need(bs, (OX - WALL/2, SMA_Y, SMA_Z), False, "H5 SMA hole is closed")
    need(bs, (OX - WALL/2, SMA_Y, SMA_Z + 6), True, "H5 no bulkhead above the SMA")
    need(hs, (OX - WALL/2, PIy0 + 10, PI_BOT_Z + 8), False, "H6 hood fills the port mouth")
    need(bs, (OX - WALL/2, PIy0 + 10, PI_BOT_Z + 8), False, "H6 plate fills the port mouth")
    px, py = lid_posts[0]
    need(bs, (px, py, WALL/2), False, "H7 corner screw hole missing")
    need(hs, (px + 2.2, py, WALL + 3), True, "H7 corner boss missing")
    need(hs, (px, py, WALL + 1.0), False, "H7 insert is not open from below")
    # Vertical lip: inner tongue on the plate, outer tongue on the hood.
    y_lap = (LAP_BASE_Y0 + HOOD_TONGUE_Y) / 2
    need(bs, ((X_IN + X_BASE_T)/2, y_lap, 12), True, "H4 base lip tongue missing")
    need(hs, ((X_HOOD_T + X_OUT)/2, y_lap, 12), True, "H4 hood lip tongue missing")
    gap_x = (X_BASE_T + X_HOOD_T) / 2
    need(bs, (gap_x, y_lap, 12), False, "H8 base fills the lip gap")
    need(hs, (gap_x, y_lap, 12), False, "H8 hood fills the lip gap")
    # The walls sit on the plate, so the two solids share a face. A 0.05 mm
    # lift is smaller than every designed gap; anything still overlapping is a
    # real collision.
    lifted = hood.translate((0, 0, 0.05))
    both = base.intersect(lifted)
    solids = both.solids().vals()
    vol = sum(s.Volume() for s in solids)
    if vol >= 1.0:
        for s in solids:
            bb = s.BoundingBox()
            print(f"H8 overlap {s.Volume():.2f} mm^3  "
                  f"X {bb.xmin:.2f}..{bb.xmax:.2f}  "
                  f"Y {bb.ymin:.2f}..{bb.ymax:.2f}  "
                  f"Z {bb.zmin:.2f}..{bb.zmax:.2f}")
    assert vol < 1.0, f"H8 hood intersects the plate by {vol:.2f} mm^3"

# ----------------------------------------------------------------------------- BUILD + EXPORT
if __name__ == "__main__":
    base = build_base()
    lid  = build_lid()
    audit(base, lid)

    # Hood prints lid-down, walls up. The outer face is flat on the bed.
    asm_bb = lid.val().BoundingBox()
    assert abs(asm_bb.zmax - (OZ + LID_T)) < 0.05, \
        f"O7 hood top is {asm_bb.zmax:.2f}, lid face is {OZ + LID_T:.2f}"
    lid_print = lid.rotate((0,0,0),(1,0,0),180)
    pre = lid_print.val().BoundingBox()
    lid_print = lid_print.translate((-pre.xmin, -pre.ymin, -pre.zmin))

    def on_bed(x, y):
        """A point 0.3 mm under the outer face must land on the bed and be plastic."""
        z = OZ + LID_T - 0.3
        p = (x - pre.xmin, -y - pre.ymin, -z - pre.zmin)
        assert lid_print.val().isInside(p), f"O7 deck is not on the bed at {p}"
        assert p[2] < 0.5, f"O7 bed face is {p[2]:.2f} mm up at {p}"

    on_bed(oled_cx, oled_cy + oled_win_y / 2.0 + 1.2)
    on_bed(OX / 2.0, WALL + 15.0)

    cq.exporters.export(base, "case_base.step")
    cq.exporters.export(base, "case_base.stl", tolerance=0.05)
    cq.exporters.export(lid_print, "case_lid.step")
    cq.exporters.export(lid_print, "case_lid.stl", tolerance=0.05)
    cq.exporters.export(lid, "_lid_asm.stl", tolerance=0.05)   # assembled, render only

    asm = cq.Assembly()
    asm.add(base, name="base", color=cq.Color(0.30,0.45,0.65))
    asm.add(lid,  name="lid",  color=cq.Color(0.75,0.78,0.82))
    asm.save("case_assembly.step")

    write_lid_qc_png("case_v4_oled_win_20261023.png")

    print(f"Outer footprint : {OX:.1f} x {OY:.1f} x {OZ+LID_T:.1f} mm (flat lid, no visor)")
    print(f"Bulkhead Y {BULK_FRONT:.1f}..{BULK_BACK:.1f}, sill Z {PI_BOT_Z:.1f}; "
          f"vertical lip {HOOD_TONGUE_Y - LAP_BASE_Y0:.2f} mm")
    print(f"Z-stack: X1200 bottom {UPS_BOT_Z:.1f} | Pi deck {PI_DECK_Z:.1f} | "
          f"cooler top {COOLER_TOP_Z:.1f} | HAT top {HAT_TOP_Z:.1f} | "
          f"stack top {STACK_TOP_Z:.1f} | lid underside {OZ:.1f}")
    print(f"OLED window {oled_win_x:.2f}x{oled_win_y:.2f} @ ({oled_cx:.1f},{oled_cy:.1f}); "
          f"pocket {oled_pocket_x:.2f}x{oled_pocket_y:.2f} deep {OLED_POCKET_D:.2f}; "
          f"button Ø{OLED_BTN_D:.1f} @ ({oled_btn_x:.1f},{oled_btn_y:.1f})")
    print(f"OLED ear bolts: 4× M2.5×8 countersunk, Ø{OLED_BOLT_D:.1f}, "
          f"nut under the ear (FR4 assumed {OLED_PCB_T:.1f} mm)")
    print(f"Cell-to-floor clearance: {PAD_H + UPS_STANDOFF_H - UPS_UNDERSIDE:.1f} mm")
    print("Exported base/lid .step/.stl (+ assembly, QC PNG). "
          "Lid STL is the hood, print-oriented (lid on the bed, walls up).")
