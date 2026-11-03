You are running the manufacturing order for the Kingfisher cabin sensor HAT, revision 0.3. The packet is the folder that contains this file:

`hardware/hat/jlcpcb/`

Read `ORDER.md`, `bom.csv`, and `positions.csv` before you open the shop. Upload `kingfisher-hat-rev0.3-gerbers.zip` with those two CSVs. The order spec in `ORDER.md` is the contract. Where a library page disagrees with that file, stop and ask Eric.

Your job runs from quote through delivery.

1. Build the JLCPCB quote exactly as `ORDER.md` specifies: five bare boards on stackup JLC04161H-7628, ENIG, green mask, 1.6 mm, four layers, outer 1 oz, inner 0.5 oz, impedance control off, no panel. Assemble two boards. Leave three bare.
2. Match every BOM line to a real part. Blank JLCPCB numbers stay blank until you have opened the part page and it matches the required manufacturer part and package. R4, D2, U2, BT1, and J3 are already numbered. D3, J1, U4, and BT2 are still blank: follow the part table in `ORDER.md`. Do not fill those with C35165, C417323, or a GNSS module other than NEO-M9N-00B.
3. Use Economic assembly if the site allows top SMT, the top right-angle header J3, and the bottom socket J1. If the Bosch BMP581 or that bottom socket forces Standard assembly, switch both assembled boards to Standard. Keep U2 on the board.
4. Turn on X-ray for the ICM-45686 and the BMP581.
5. Re-upload `positions.csv` from this folder. Its Y values are the Gerber Y values (negative). In the assembly preview, check every row in the placement table in `ORDER.md`. U4 pin 1 has to be at (154.50, −68.50) and the RF pin at (154.50, −55.60). U2, U3, U5, and U6 are rotation 270: U2 pin 1 at (112.25, −66.26); U3 pin 1 at (129.36, −58.95) and VOUT at (131.64, −58.95); U5 pin 1 at (120.85, −59.35); U6 anode at (117.55, −71.15) and cathode at (117.55, −69.85). J3 is rotation 90, pin 1 at (153.50, −78.00), pins toward increasing X. J1 pin 1 has to be on the bottom at (108.37, −48.77) and pin 2 at (108.37, −46.23), with the socket center at (132.50, −47.50). D1 and D2 cathodes have to be on the lower-X pad. BT2’s positive terminal has to be on (126.88, −67.88). Do not flip those Y signs back to positive, and do not mirror X.
6. Ask Eric for the shipping address. Ask whether the paper fit on the real Pi stack and the antenna DC check are done. Show him the full quote, including part lines, lead time, and shipping. Wait for an explicit yes before anyone pays. Point out a total above $600, and point out a total that is low because the GPS module or the IMU is missing.
7. After he pays, watch the order. Answer factory questions from this packet. If a question is not answered here, ask Eric. Report the build status, the ship date, and the tracking number when they exist.
8. When the boards arrive, tell Eric: insert the CR2032 with the positive face up, the ML414H is already on the board, and the antenna jack is U.FL.

Do not change, regenerate, or commit any design file. Do not add R7, R8, the test points, a CR2032, screws, or an antenna to the order. Do not assemble five boards. Do not turn on paid impedance control. Do not accept a substitute GNSS module, IMU, barometer, RTC, regulator, voltage reference, inductor, Schottky, or backup cell. Do not place Infineon ESD0P2RF-02LS or any ~1 pF ESD diode on D3. Do not replace the 34.0 kΩ resistor with 33 kΩ. Do not move J1. Do not fit Keystone 3003. If the ML414H or a legal D3 is unavailable, stop and ask; do not leave either off without a yes.
