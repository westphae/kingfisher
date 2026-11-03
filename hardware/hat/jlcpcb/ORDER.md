# Kingfisher cabin HAT rev 0.3 — JLCPCB order

Upload `kingfisher-hat-rev0.3-gerbers.zip`, `bom.csv`, and `positions.csv` from this folder.

## What to buy

Five bare boards. Assemble two of them. The other three stay bare.

| Choice | Value |
| --- | --- |
| Shop | JLCPCB |
| Layers | 4 |
| Thickness | 1.6 mm |
| Stack | JLC04161H-7628. Outer copper 1 oz. Inner copper 0.5 oz. Outer dielectric is 7628 |
| Finish | ENIG |
| Mask / silk | Green / white |
| Impedance control | Off. The 0.32 mm GPS trace is already drawn for this stack |
| Outline | 65 × 56 mm with a left notch and an internal slot. No panel, no v-score |
| Assembly | Economic if the quote allows it. Top SMT, top through-hole J3, bottom through-hole J1 only |
| X-ray | On for U1 and U2 |

The Gerber job names the inner layers L2 and L3. A viewer that shows only two copper layers is the wrong read of the files.

## Parts that must be the named device

A blank JLCPCB number in `bom.csv` is intentional. A wrong number is worse than a blank one. Match these by the manufacturer part, then confirm the package.

| Ref | Required part | Reject |
| --- | --- | --- |
| U4 | u-blox NEO-M9N-00B | Any other GNSS module. Their library line C9900006872 is only acceptable when the quote page says that exact u-blox part and shows a price and a lead time |
| U1 | TDK ICM-45686, LGA-14, 3.0 × 2.5 mm. Library C22459454 | The other ICM line C9900251359 |
| U2 | Bosch BMP581, LGA-10, 2.0 × 2.0 mm. Library C5362283. Standard only, and it needs X-ray. CPL rotation 270 | BMP390, BMP280, or any other barometer. Do not use C9900017788. If C5362283 forces Standard assembly, assemble both boards as Standard |
| U5 | Micro Crystal RV-3028-C7, DFN 1.5 × 3.2 mm. Library C2829066 | A different RTC or a different package |
| U3 | Diodes AP2112K-3.3TRG1, SOT-23-5. Library C51118 | A different voltage or a SOT-89 |
| U6 | TI LM4040C30IDCKR, 3.00 V, SC-70-5. Library C701882 | A 2.5 V or 3.3 V reference, or a 3-pin package |
| L1 | Murata LQG15HS27NJ02D, 27 nH, 0402. Library C12669 | A ferrite bead, or a 27 nH part in another package |
| D1 | Nexperia PMEG4005EJ in SOD-323F. Library C426868 | The SC-74 part PMEG4005EJ-QX |
| D3 | Bidirectional ESD diode in SOD-882 / DFN1006-2 (body 1.0 × 0.6 mm), standoff at least 5 V, capacitance max 0.5 pF. The Nexperia part is PESD5V0C1BLS-QYL, C5311564, capacitance max 0.3 pF. Use it only if the quote page is that part and the preview body is 1.0 × 0.6 mm. It has no EasyEDA model to check, so stop if the line will not attach | Infineon ESD0P2RF-02LS (that part is 0.62 × 0.32 mm and does not fit these pads). Leiditech C5120676 (that package is 0603). Any diode at about 1 pF. Do not use C417323: its library link points at a Littelfuse datasheet |
| BT2 | Seiko ML414H-IV01E | A different coin cell. If it is not in the library, ask before consigning it and before leaving it off |
| BT1 | Keystone Electronics 3002, the SMT 20 mm clip. Library C5503436, and only if the page is that Keystone clip | The through-hole Keystone 3003. Do not place a CR2032 |
| J2 | Hirose U.FL-R-SMT-1 board receptacle. Library C598199 is the (01) reel of that receptacle | A cable plug, or an SMA |
| R4 | 34.0 kΩ, 1%, 0603. Library C843946, Vishay CRCW060334K0FKEA | 33 kΩ or 34.8 kΩ |
| D2 | Yellow-green 0603, Lite-On LTST-C190KGKT. Library C125094. The cathode is pin 1 | A part whose cathode is on the other pad at rotation 0 |
| J1 | 2×20 female socket, 2.54 mm pitch, pins that fit a 1.0 mm hole. Placed on the bottom. Pin 1 on (108.37, −48.77) and pin 2 on (108.37, −46.23) | A male header, or a shrouded box header. Do not use C35165: when its holes line up, its pin 1 is on our pin 2 |
| J3 | 1×5 male pin header, 2.54 mm, right angle, pins that fit a 1.0 mm hole. Library C3040097. CPL rotation 90, so the pins point toward increasing X. The mating pins are 10.9 mm | A vertical header |

The passives that already have a library number are basic-library values (100 nF 16 V X7R 0402, 1 µF 50 V X5R 0603, and the listed 1% resistors). If a number no longer matches that value and package, keep the value and package.

R5 is 33 Ω in 0402 on purpose. Leave the value. A sustained shorted antenna dissipates about 0.3 W in that resistor; do not "fix" it with a different value on this order.

## Do not place

R7 and R8 (4.7 kΩ) are on the board and marked do-not-place. TP1–TP13 are bare pads. MH1–MH4 are M2.5 mounting holes. None of these are in the BOM or the placement file. No CR2032. No screws, no standoffs, no antenna, no pigtail.

## Placement check

`positions.csv` is in the Gerber coordinate system: same X as the board file, and Y negated. The viewer reading on the J1 pads, about (156.66, −48), is that system. J1 and J3 rows are the center of the hole pattern. Every other row is the footprint center. U4, J1, U2, U3, U5, U6, and J3 rotations are the JLCPCB library angles, not the KiCad angles. U2, U3, U5, and U6 are 270 so pin 1 sits at the top of the left-hand column. J3 is 90. Before paying, the assembly preview has to put these pads on these coordinates:

| Ref | What must land there |
| --- | --- |
| U4 pin 1 | (154.50, −68.50). RF pin 11 at (154.50, −55.60). CPL rotation is 0 for JLCPCB’s NEO model. A model whose pin-1 mark is on the opposite corner has the antenna on the wrong pad |
| U1 pin 1 | (110.84, −58.75) |
| U2 pin 1 | (112.25, −66.26). CPL rotation 270 |
| U3 pin 1 (VIN) | (129.36, −58.95). CPL rotation 270. The three-pin side faces decreasing X, and pin 1 is the top pad on that side. VOUT is (131.64, −58.95). The lower pad on that side, pin 4, is open |
| U5 pin 1 | (120.85, −59.35). CPL rotation 270. Pin 1 is CLKOUT at the top of the left column. SCL and SDA are the two lower pads on that side |
| U6 pin 1 (anode) | (117.55, −71.15). CPL rotation 270. Pin 1 is the top of the three-pin side. Pin 3, the cathode, is (117.55, −69.85) |
| D1 cathode | (133.40, −64.20), the lower-X pad |
| D2 cathode | (133.01, −67.80), the lower-X pad |
| D3 | Either way around. The body has to be the 1.0 × 0.6 mm SOD-882 |
| J2 center pin | (160.15, −55.60), on the GPS trace. The body sits toward increasing X |
| BT2 positive | (126.88, −67.88) |
| BT1 | Both metal feet on the VBAT pads at (111.2, −83.0) and (136.8, −83.0). The round center pad is ground |
| J1 pin 1 | Bottom side, (108.37, −48.77). Pin 2 is (108.37, −46.23). The socket center in the CPL is (132.50, −47.50). The body has to lie along the pad row. Rotation 0 is only right for a model that already does that with pin 1 on the first coordinate |
| J3 pin 1 | (153.50, −78.00). CPL rotation 90. The right-angle pins point toward increasing X, off the board edge |

J1 is the only bottom part. Its row is the center of the pads, not pin 1. If the body is centered on pin 1 and hangs off the pad row, that one row should move to (108.37, −48.77); J3’s matching anchor is (153.50, −78.00). Do not mirror X and do not flip the Y sign back.

## Before payment

Ask Eric for the ship-to address. Ask whether the paper fit on the real stack and the antenna DC check are done. Wait for an explicit yes on the quote. Show the line items. Call out a total above $600, and also call out a total that is low because U1 or U4 was dropped.

An order number printed by the fab goes on the bottom, clear of the socket and the mounting holes, not on the GPS trace.

## After the boards ship

Report the ship date and the tracking number. On arrival Eric inserts the CR2032 with the positive face up. The ML414H is already soldered. The antenna connector is U.FL. A U.FL-to-SMA pigtail is a separate purchase if the aircraft antenna is SMA.
