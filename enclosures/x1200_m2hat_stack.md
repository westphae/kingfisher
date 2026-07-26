# X1200 UPS + M.2 HAT+ stack — measured dimensions (2026-07-17)

Eric's caliper measurements of the actual hardware, cross-checked against the
[Geekworm X1200 wiki](https://wiki.geekworm.com/X1200). Input data for the
`case/pi5_aviation_case.py` rework. Reference frame = Pi orientation:
**front** = USB-C/HDMI edge, **right** = Ethernet/USB edge, **left** = microSD
edge, **back** = GPIO edge. X1200 mounts beneath the Pi in the same orientation.

## X1200 UPS (beneath Pi)

| # | Item | Value |
|---|------|-------|
| 1 | Cell orientation | 18650s face **down** (toward case floor) |
| 2 | PCB thickness | 1.6 mm |
| 3 | X1200↔Pi standoff (kit) | 4.5 mm (pogo-pin engagement height) |
| 4 | Underside depth (PCB bottom → cell/holder bottom) | 18.5 mm |
| 5 | Tallest Pi-facing parts | pogo pins, then the 4.5 standoffs |
| 6 | Board L×W | 85 × 56 (matches Pi); only the power button overhangs, 2.7 mm on the **left** edge |
| 7 | Mounting holes | match Pi 58×49 pattern; **threaded** (M2.5) |
| 8 | USB-C charge input | **front** edge, center 32.5 from right edge (= 52.5 from left), 8.8 w × 3.1 h, on PCB **top** side. Wiki: 5 Vdc 5 A in, 2.3–3.2 A max charge |
| 9 | Power button | momentary push (mirrors Pi power button; auto power-on enabled by default), **left** edge, 14.0 from back, protrudes 2.7; center 4.0 mm **below** PCB bottom |
| 10 | LEDs | **left** edge top-side: bank 13.0 wide centered on face, charge LED 10.0 from front (wiki: 25/50/75/100% charge level + power status). **Back** edge: two LEDs at 20 and 25 from left, top side |
| 11 | Electrical hookups used | I2C fuel gauge (wiki: Maxim @ 0x36) + power-loss GPIO, wired X1200 → Pi. XH2.54 5V outputs unused |
| 12 | Fastening | see mounting scheme below |

## M.2 HAT+ (above Pi, over the Active Cooler)

| # | Item | Value |
|---|------|-------|
| 13 | Standoffs | 16 mm |
| 14 | Tallest above HAT top | extended GPIO pins +9.0; IDC ribbon connector (when attached) is 15.4 tall sitting on a pin-retention strip 4.0 above HAT top → ribbon stack tops out **19.4 above HAT** |
| 15 | Ribbon between Pi and HAT? | gap is 16.0, connector 15.4 — no working pin engagement in the gap; not viable as-is |
| 16 | FPC (PCIe) ribbon | **left** side, bulges ~5 mm past HAT edge |
| 17 | Mounting | all 4 holes, 58×49 pattern |

## Whole stack

| # | Item | Value |
|---|------|-------|
| 18 | Bench total, battery bottom → GPIO pin top | **52.2** (computed from rows above: 18.5+1.6+4.5+1.6+16+1.6+9 = 53.3; ~1.1 mm disagreement → keep ≥1.5 mm design margin on heights) |

## Mounting scheme

Prior art for this exact stack
([Omer Droub, Printables 1288473](https://www.printables.com/model/1288473-case-for-raspberry-pi-5-with-geekwormsuptronics-x1),
[Thingiverse 7031272](https://www.thingiverse.com/thing:7031272)) uses M-F
standoffs threaded into the X1200 bosses from below plus M2.5×20 screws from
the Pi top. **Eric's measurements show that scheme doesn't close**: the boss
barrel is only ~6 mm of thread (4.5 post + 1.6 PCB), a 6 mm stud from below
fills essentially all of it, and the top screw has nothing left to grab —
the two fasteners fight over the same threads from opposite ends.

A second refinement (Eric again): a "bolt from the Pi top" is impossible —
the HAT spacers occupy the same four holes. The corner holes serve the whole
tower, so the bolt must start at the **HAT top** and the budget is:
HAT 1.6 + spacer 15.8 (measured; 16 nominal) + Pi 1.6 + barrel 6.1 = **25.1 mm
to exit the barrel**. M2.5×20 → 1 mm of post engagement; M2.5×25 → fills the
barrel but leaves nothing to attach the case standoff. Hence:

**Adopted scheme — one M2.5×30 through-bolt per corner (full-column clamp):**

1. 4× **M2.5×20 female-female standoffs** (hex brass; through-threaded or
   blind-tapped ≥6 mm/end) below the X1200. 20 mm floats the 18.5 mm cells
   1.5 mm over the floor (case pads add another 2 mm).
2. 4× **M2.5×30 bolts from the HAT top**: HAT → spacer → Pi → *through* the
   X1200 barrel → ~4.9 mm into the standoff top. One torqued column per
   corner; the barrel's thin threads are captured, not load-bearing. The bolt
   threads through ~22 mm of female thread (spacer + barrel) — optionally swap
   the HAT's threaded spacers for plain 16 mm tube spacers to avoid that.
3. 4× **M2.5×8 pan-head case screws from below** through the counterbored
   floor+pad (2.3 mm) into the standoff bottom (~5.7 mm engagement).
   Tip-collision check inside the standoff: 4.9 + 5.7 = 10.6 ≤ 20 → 9.4 clear.

Assembly: build the tower on the bench, spin the standoffs onto the
protruding bolt tips *before* torquing (aligns the threaded segments), torque,
drop in, base screws last. Loose joints → poor pogo contact. The 4.5 mm posts
are **fixed to the X1200** (confirmed). Bolt heads on the HAT top (~2.5 mm)
stay far below the IDC connector — no case-height impact.

## Case design decisions

- microSD access: **dropped** (NVMe boot).
- Cooling: keep lid grille + add side grilles; fan rarely runs but help it.
- Left wall: LED **light pipes** (side bank), recessed finger hole for the
  power button (guard against inadvertent press), FPC ribbon needs ~5 mm of
  left interior clearance (CLR_L 1.0 → ~6).
- Back wall: light pipes for the two back LEDs.
- Front wall: UPS USB-C should sit ~**flush** for easy cable insertion → local
  interior wall pad around the port (global CLR_F stays Ethernet-limited).
- Bottom exterior stays flat for Dual-Lock → counterbore the 4 base screws.
