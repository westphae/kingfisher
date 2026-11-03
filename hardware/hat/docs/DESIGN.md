# Kingfisher Sensor HAT — design notes (rev 0.3)

Rev 0.3 replaces the rev 0.2 sheet. That sheet did not connect the circuit
(power landed on ground pins, and most pins were open). Do not fab rev 0.2.

This revision is routed on JLCPCB stackup JLC04161H-7628. Error-level DRC is
clean: no clearance, hole, or unconnected items. Gerbers and drills are in
`fab/`.

Impedance control on the order is optional. The RF width below is already
the width for this stack. Confirm the mechanical checks in the next section
before ordering; the board file cannot prove those.

## What this board is

A thin sensor HAT on the extra-tall GPIO pins that already stick through the
official M.2 HAT+. The female socket is on the **bottom**. It is not a HAT
between the Pi and the M.2 board. It has to stay under the current ribbon,
about 19.4 mm above the M.2 board. The pins are 9.0 mm above that board.

It replaces the cabin SparkFun NEO-M9N and ICM-45686 breakouts. The wing-pod
magnetometer stays on the pod. Cabin humidity and CO are out of scope.

There is no cell UPS on this HAT. The X1200 already holds the 5 V rail through
the pogos. A cell on top of the stack would fight that, cannot feed a Pi 5
plus NVMe, and does not fit the height.

## Stack

```
X1200 UPS → Pi 5 → Active Cooler → M.2 HAT+ → [extended GPIO pins +9 mm]
                                              → this HAT (female socket on bottom)
```

Before ordering, confirm three mechanical things this repo cannot check:

- 9 mm of the extended header actually engages the chosen female socket.
- Pin 1 of that socket matches the template pose (J1 is not moved).
- The Keystone 3002 plus a CR2032 clears the lid. The holder is about 5–6 mm tall.

## Circuits

### GPS

The module is the same NEO-M9N as the flying SparkFun SMA board. The parts
that the u-blox datasheet (UBX-19014285) and integration manual
(UBX-19014286) specify are taken from those manuals.

- AP2112K-3.3 from Pi 5 V. EN is tied to 5 V. 1 µF on the input, 1 µF and
  100 nF on the output. That rail is `+3V3_GPS`.
- NEO-M9N `VCC` is on `+3V3_GPS`. USB is unused, so `V_USB` (pin 7) is tied
  to ground and `USB_DM` / `USB_DP` stay open.
- `D_SEL` pulled to `+3V3_GPS` with 10 kΩ, so the module stays on UART+I²C.
  SPI is not wired.
- 33 Ω series on TX and RX. Pi TX (header pin 8) goes to NEO RX (pin 21).
  NEO TX (pin 20) goes to Pi RX (header pin 10).
- PPS is NEO TIMEPULSE to GPIO18.
- GPS I²C (module address 0x42) is left open. It must not join the Pi bus.
- `SAFEBOOT_N` goes only to TP13. Leave it open in normal use; pull it low
  to enter safeboot.
- `LNA_EN`, `EXTINT`, `RESET_N`, and the reserved pins stay open. `LNA_EN`
  open keeps the antenna bias on whenever `VCC` is on.
- Backup is a soldered ML414H directly on `V_BCKP` (no diode in the backup
  path), with 1 µF on that pin. Charge is `+3V3_GPS` → 2.2 kΩ → LM4040-3.0
  cathode (`VCHG`) → 34 kΩ → PMEG4005EJ anode, cathode → `V_BCKP`. The
  reference holds the float at 3.00 V. The 34 kΩ keeps a cell at 2.0 V under
  30 µA even with the diode drop at zero. The diode keeps the cell from
  discharging into the reference when the rail is off.
- Antenna bias is the u-blox network: `VCC_RF` → 33 Ω → bias node, 100 nF
  from that node to ground, Murata LQG15HS27NJ02D (27 nH, 0402) from the
  bias node onto `RF_IN`. `RF_IN` is also tied to the U.FL center. The
  inductor is the bias choke. It is not in series with the RF path, and it
  is not on `VCC`. An Infineon ESD0P2RF-02LS (0.2 pF, SOD-882) sits from the
  RF node to ground at the connector.

The ML414H charges at 2.8–3.1 V, characterized at 3.1 V and 30 µA max. A
resistor straight from 3.3 V does not clamp the float, so the shunt is the
clamp. Do not use this charge path on the CR2032.

The 33 Ω is the `VCC_RF` short-circuit limit (about 100 mA), not the 10 Ω
row in the bias-T table. The LQG15HS 27 nH part is the series u-blox names;
its self-resonant frequency is 1.7 GHz, so the impedance at L1 is above the
ideal 27 nH reactance.

The bias-T is always connected. A passive antenna with a DC short would load
`VCC_RF`. Confirm the aircraft antenna is active (or at least DC-open)
before relying on this.

`I_BCKP` is 45 µA typical at `V_BCKP` = 3 V with `VCC` off (UBX-19014285
Table 11). The 1.0 mAh ML414H is about a day of hot-start memory. It is not
the multi-year clock battery.

### IMU

ICM-45686 on Pi 3.3 V, I²C 0x68. `AP_AD0` is ground, `AP_CS` is 3.3 V, INT1
is GPIO17 (active-low, `int_trigger=8`). 100 nF on VDD and a separate 100 nF
on VDDIO. INT2 and the reserved pins are open. INT2 can be an FSYNC input,
but this board does not spend a wire on it; PPS already disciplines the
clock that stamps samples.

The land pattern is `Kingfisher:ICM-45686`. Pad centers are the IPC LGA-14
centers (±1.1625 mm). Pin numbers are the TDK top view in DS-000577, not the
Bosch BMI160 footprint (that footprint mirrors the pin numbers). Adjacent pads
are about 0.175 mm apart. Board clearance is 0.15 mm. JLCPCB's minimum
spacing on this process is 0.127 mm. The axis silk on the footprint is 0.3 mm
tall on purpose; the package is only 2.5 mm.

Silk axes, footprint rotation 0 as placed: **+X toward the GPS module, +Y
toward the coin-cell holder, +Z out of the top of the HAT.** Pin 1 is toward
the left notch and toward +Y. Do not copy the AHRS mount matrix from the
ribbon breakout. Measure it from this silk.

### Barometer

BMP581 on a local `BMP_VDD` rail, I²C 0x47. `VDD`, `VDDIO`, `CSB`, and `SDO`
all sit on that rail, so chip-select is high at power-up and the address is
0x47. The on-chip CSB pull-up is off at reset, so the external tie is
required. Pi 3.3 V reaches `BMP_VDD` through 10 Ω (R9) because a supply edge
faster than 10 µs needs that series resistor. 100 nF on `BMP_VDD` at each
end of the package (C3, C4). INT is open. Do not tie it to ground; the pin
can be driven. No vias under the package.

### RTC battery

A second cell, not the GPS cell. Replaceable CR2032 in a Keystone 3002 feeds
only RV-3028-C7 `VBACKUP` (I²C 0x52). There is no charge path into the CR2032.
Do not tie this cell to the Pi 5 `rtc_bbat` charge pin.

Kingfisher's clock code reads the Pi's `rtc0` only. The RV-3028 needs a later
`dtoverlay=i2c-rtc,rv3028` (or the Pi 5 equivalent). Until that overlay
exists, the chip is on the bus and keeps its own time, and the recorder still
uses the Pi clock.

DS3231 is the wrong RTC: the usual address is 0x68, which is the IMU.

### X1200 harness

J3 is a 5-pin header so the existing X1200 cable still lands. Pin 1 is the
pad closest to the GPS module:

| Pin | Net |
|-----|-----|
| 1 | +3V3 |
| 2 | GND |
| 3 | SDA |
| 4 | SCL |
| 5 | GPIO6 (header physical pin 31, X1200 PLD) |

X1200's MAX17040 stays at 0x36 on this same I²C bus. GPIO16 (charge disable)
is not claimed.

### Header pins that software uses

| Pi header | Net | Where it goes |
|-----------|-----|----------------|
| 1 and 17 | +3V3 | IMU, BMP581, RTC, DNP pull-ups |
| 2 and 4 | +5V | AP2112 input and EN |
| 3 | SDA | IMU, BMP581, RTC, J3 |
| 5 | SCL | IMU, BMP581, RTC, J3 |
| 6, 9, 14, 20, 25, 30, 34, 39 | GND | common |
| 8 | UART_TX | through 33 Ω to NEO RX |
| 10 | UART_RX | through 33 Ω from NEO TX |
| 11 | IMU_INT | ICM INT1 |
| 12 | GPS_PPS | NEO TIMEPULSE |
| 31 | PLD | J3 pin 5 |

Header pin 17 is 3.3 V. The rev 0.2 generator treated it as ground. ID_SD and
ID_SC (pins 27 and 28) are open: there is no HAT EEPROM.

I²C pull-ups are 4.7 kΩ, DNP (R7, R8). The Pi already has pull-ups. One green
LED on `+3V3_GPS` through 1 kΩ.

### Cabin I²C map

| Addr | Device |
|------|--------|
| 0x36 | X1200 MAX17040 (existing) |
| 0x47 | BMP581 |
| 0x52 | RV-3028-C7 |
| 0x68 | ICM-45686 |

## Test points

TP1–TP8 are the row under the GPIO header, left to right: `+3V3`, `+5V`,
`+3V3_GPS`, `V_BCKP`, `VBAT`, `SDA`, `SCL`, `UART_TX`. TP9–TP12 are the column
just left of J3, top to bottom: `UART_RX`, `GPS_PPS`, `IMU_INT`, `GND`.
TP13 is `SAFEBOOT_N` on the NEO, next to the module. It is not strapped.

## Fabrication

Order JLCPCB **JLC04161H-7628** (1.6 mm, 4-layer). Finished thickness in the
board file is 1.5862 mm. Outer copper is 1 oz. Inner copper is 0.5 oz.

| Layer | Material | Thickness | Er |
|-------|----------|-----------|----|
| F.Cu | copper | 0.035 mm | |
| prepreg | 7628 | 0.2104 mm nominal | 4.4 |
| In1.Cu | copper | 0.0152 mm | |
| core | FR4 | 1.065 mm | 4.6 |
| In2.Cu | copper | 0.0152 mm | |
| prepreg | 7628 | 0.2104 mm nominal | 4.4 |
| B.Cu | copper | 0.035 mm | |

The RF trace is designed at the pressed outer-dielectric height JLCPCB
publishes for this stack, 7.1 mil (0.180 mm) at Er 4.4, not at the 0.2104 mm
nominal sheet thickness. Width is **0.32 mm**. Bare microstrip impedance is
about **50.2 Ω** (Hammerstad–Jensen, mild thickness correction). Soldermask
pulls that down about 1–2 Ω; 50.2 Ω is the bare number. A normal order
without paid impedance control still wants this width.

The trace runs on F.Cu from the NEO `RF_IN` pad at (154.50, 55.60) to the
U.FL center at (160.15, 55.60). A stub at x=157.70 rises to the 27 nH choke
at (157.70, 56.56). A second stub goes to the ESD diode at (157.85, 53.00).
Top ground is kept off that copper. In1 under the trace stays solid. Stitch
vias sit outside the keepout, about 1.35 mm off the trace.

Ground is poured on all four copper layers. Copper stops 0.3 mm inside the
outline, including the notch and the slot. Pads connect solid, with no
thermal gap, so the header ground pins reach the planes through the gaps
between the 1.7 mm lands. An SMD ground island that the pour splits off gets
a stitch via into the inner planes. The header pins are plated through and
already reach In1.

Signal traces are 0.20 mm. `+3V3`, `+5V`, and `+3V3_GPS` are 0.35 mm. Vias
are 0.6 mm with a 0.3 mm drill. Clearance is 0.15 mm (0.20 mm on the RF net).

`fab/` holds the Gerbers (Protel names, X2) and the Excellon drills, plated
and non-plated separate. The X2 headers say L1 top, L2 In1, L3 In2, L4
bottom. KiCad 9.0.2 writes L5 and L7 for those inner layers in the
`.gbrjob`; the copy in `fab/` is corrected to L2 and L3. Fix that again
after a re-plot. Plot them again after any route:

```bash
kicad-cli pcb export gerbers --output hardware/hat/fab \
  --layers F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts \
  hardware/hat/kingfisher-hat.kicad_pcb
kicad-cli pcb export drill --output hardware/hat/fab \
  --excellon-separate-th --generate-map --map-format gerberx2 \
  hardware/hat/kingfisher-hat.kicad_pcb
```

## What was checked

`scripts/check_netlist.py` compares the exported netlist to the datasheet pin
numbers. The rev 0.3 sheet passes: 69 nets. That check is the contract.
`kicad-cli sch erc` segfaulted on the old sheet and is not the proof.

On the board, J1 pad 1 is (108.37, 48.77) and pad 40 is (156.63, 46.23), on
the bottom, which is the template pose. Mounting holes stay at (103.5, 47.5),
(161.5, 47.5), (103.5, 96.5), and (161.5, 96.5). `kicad-cli pcb drc
--severity-error` reports 0 violations and 0 unconnected items.

Warnings that remain are intentional. U1's axis silk is smaller than the
0.8 mm board-text minimum because the package is 2.5 mm. J1 is the HAT
template socket, so it does not match the stock `Connector_PinSocket_2.54mm`
library copy. A few signal vias have a track on the top only; those nets are
still connected, and the vias are not shorts to the planes.

## Regenerate

```bash
python3 hardware/hat/scripts/build_libs.py
python3 hardware/hat/scripts/build_sch.py
kicad-cli sch export netlist -o /tmp/kingfisher-hat.net hardware/hat/kingfisher-hat.kicad_sch
python3 hardware/hat/scripts/check_netlist.py /tmp/kingfisher-hat.net
python3 hardware/hat/scripts/place_pcb.py
python3 hardware/hat/scripts/route_pcb.py
```

`place_pcb.py` and `route_pcb.py` need system `python3-pcbnew` (KiCad 9).
`place_pcb.py` keeps J1 and the mounting holes and replaces every other
footprint. It deletes tracks. Run `route_pcb.py` after it. `route_pcb.py`
routes again from the placed board and rewrites the JLC04161H-7628 stackup.

Schematic Y is down and symbol Y is up. The generator accounts for that.
After any move of a schematic part, rerun the netlist check. A wire that
joins the wrong pin will not be obvious on the sheet.
