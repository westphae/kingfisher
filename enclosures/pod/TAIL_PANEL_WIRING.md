# Pod aft panel to the Battery Babysitter

How the three parts on `tail_panel.stl` connect to the SparkFun Battery
Babysitter (PRT-13777). The plate is populated on the bench. This note is the
wiring. The cutouts are L6 in `REQUIREMENTS.md`.

The Babysitter is a BQ24075 charger plus a BQ27441 gauge. Its output, marked
**VOUT** + and −, is the pod supply. That voltage moves with the cell and with
USB, about 3 V to 5.5 V. Both lamps need a resistor in the wire. There are no
spare pads on the board for those resistors. Heat-shrink each resistor.

The output pads are the holes labeled **V OUT** + and −, on the same end of the
board as the I²C row (GND, VPU, SDA, SCL). Three footprints share that net: a
screw terminal, a 0.1 inch header, and a JST pad. Solder to whichever of those
is open. VOUT − is ground.

A 5 mm LED's long lead is the anode.

## Rocker (COM-08837)

The rocker connects the battery to the pod. It does not switch VOUT by itself.

Find the onboard slide switch. The single hole beside it is **JP12**. The word
**SYSOFF** is printed on the back of the board next to that hole. JP12 is the
BQ24075 SYSOFF pin.

Leave the onboard slide in **OFF**. In that position the board holds SYSOFF up
to the battery through 100 kΩ. The ON position ties SYSOFF straight to ground,
and the panel rocker then does nothing.

Wire the rocker between JP12 and ground (VOUT − is ground). Use the two
terminals that close when the rocker is on. If the rocker has a third terminal,
leave it empty.

- Rocker on: SYSOFF is grounded. The battery is connected to VOUT, and charging
  is allowed.
- Rocker off: SYSOFF rises to the battery through the 100 kΩ. The battery is
  disconnected from VOUT, and charging stops.
- USB plugged into the Babysitter's micro-B brings VOUT up even with the rocker
  off. The rocker does not kill the pod while a charger is connected. The cell
  charges only with the rocker on.

## Red lamp (pod powered)

This lamp is on whenever VOUT is up.

VOUT + → 1 kΩ → long lead. Short lead → VOUT −.

At 3–5.5 V that is about 1–4 mA. The onboard red LED, silk **PWR**, is a
separate lamp selected by the **PWR-LED SEL** jumper (PWR or !PGD!). Leave that
jumper alone. The panel lamp does not use it.

## Blue lamp (charging)

This lamp follows the charger's **!CHG** pin: low while the cell is charging,
open when charging is finished or disabled.

!CHG is not a header. On the board it is the BQ24075 pin and one end of **R9**,
the 180 Ω resistor just inboard of the onboard blue LED marked **CHG**. The
onboard lamp already runs from VOUT through that LED and R9 into !CHG. The pin
can sink 15 mA absolute maximum, and 180 Ω already uses most of that when VOUT
is near 5 V. A second lamp on the same pin crowds the limit.

Lift R9. Then:

VOUT + → long lead. Short lead → 1 kΩ → the R9 pad that faces the middle of
the board (the pad that went to the chip, not the pad that went to the onboard
LED).

The lamp is on while the cell is charging and off when charging finishes. With
the rocker off and USB connected, this lamp can still glow even though the cell
is not charging. That is how the BQ24075 behaves. SparkFun notes the same thing
on the onboard LED.

## What stays off these wires

Do not tie either LED straight across VOUT. Do not use the 1.8 V output. Do not
solder the blue lamp to the onboard LED's cathode. That node is on the LED side
of R9, and the two lamps would share 180 Ω.
