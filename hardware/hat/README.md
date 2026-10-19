# Kingfisher Sensor HAT (rev 0.3)

Custom HAT for the extra-tall GPIO header above the M.2 HAT+. It replaces the
cabin SparkFun NEO-M9N and ICM-45686 breakouts. The schematic netlist is
checked. The board is routed on JLCPCB stackup JLC04161H-7628. Gerbers
and drills are in `fab/`. Confirm header engagement, the CR2032 against the
lid, and that the GNSS antenna is active before ordering.

| Ref | Part | Role |
|-----|------|------|
| U1 | ICM-45686 | Cabin IMU, I²C `0x68`, INT1 → GPIO17 |
| U2 | BMP581 | Extra cabin baro, I²C `0x47` |
| U3 | AP2112K-3.3 | 5 V → 3.3 V for the GPS only |
| U4 | NEO-M9N | GNSS, UART0 + PPS on GPIO18 |
| U5 | RV-3028-C7 | I²C RTC at `0x52` |
| BT1 | CR2032, Keystone 3002 | Replaceable cell for the RTC `VBACKUP` only |
| BT2 | ML414H | Soldered rechargeable cell on GPS `V_BCKP` |
| D1 | PMEG4005EJ | GPS backup charge diode |
| D2 | Green LED | `+3V3_GPS` through 1 kΩ |
| J1 | 2×20 socket, bottom | Mates the extended Pi header. Do not move it |
| J2 | U.FL | Antenna. 0.32 mm microstrip plus the bias-T stub |
| J3 | 1×5 header | X1200 harness: +3V3, GND, SDA, SCL, GPIO6 |

Outline is the official Pi HAT (65 × 56.5 mm, M2.5 on 58 × 49 mm).

The GPS circuit follows the SparkFun NEO-M9N SMA board: LDO with EN on 5 V,
UART pull-up on `D_SEL`, 33 Ω on TX/RX, ML414H directly on `V_BCKP`, and the
ferrite bias-T into the antenna. The CR2032 does not back up the GPS and has
no charge path. There is no UPS on this board. See
[docs/DESIGN.md](docs/DESIGN.md).

## Open in KiCad

KiCad 9 (generated against 9.0.2).

```bash
kicad hardware/hat/kingfisher-hat.kicad_pro
```

Libraries:

- `libs/Kingfisher.kicad_sym` — ICM-45686, BMP581, NEO-M9N
- `libs/Kingfisher.pretty` — ICM-45686 (TDK pin numbers), BMP581, ML414H

Regenerate with system Python, not a uv venv. `pcbnew` comes from
`python3-pcbnew`.

```bash
python3 hardware/hat/scripts/build_libs.py
python3 hardware/hat/scripts/build_sch.py
kicad-cli sch export netlist -o /tmp/kingfisher-hat.net hardware/hat/kingfisher-hat.kicad_sch
python3 hardware/hat/scripts/check_netlist.py /tmp/kingfisher-hat.net
python3 hardware/hat/scripts/place_pcb.py
python3 hardware/hat/scripts/route_pcb.py
```

`check_netlist.py` is the schematic check. It passed for rev 0.3 (64 nets).
`place_pcb.py` keeps the template socket and mounting holes and deletes
tracks. `route_pcb.py` restores the route and the 4-layer stackup.

## Status

Done:

- Schematic for the GPS replica, TDK IMU land, BMP581, RV-3028, both backup
  cells, X1200 header, test points, and the GPS power LED
- Netlist check against datasheet pin numbers
- Parts placed on the HAT outline, clear of the notch, the slot, the mounting
  holes, and the header
- Routed, including the 0.32 mm U.FL microstrip (bare Z0 about 50.2 Ω at the
  pressed 7.1 mil / Er 4.4 height). Soldermask pulls that down about 1–2 Ω
- JLC04161H-7628 stackup filled in. Error-level DRC is clean
- Gerbers and drills in `fab/`

Still to confirm on the aircraft, and still open in software:

- Female-header engagement, lid clearance over the CR2032, pin 1 against the
  real extender, and whether the GNSS antenna is active
- `i2c-rtc` for the RV-3028, a cabin BMP581 reader, and an AHRS mount matrix
  measured from this board's silk

## Attribution

- Outline and GPIO socket: KiCad Raspberry Pi HAT template
- BMP581 pads: SparkFun Qwiic BMP581 hardware, CC-BY-SA 4.0
- ML414H pads and the GPS power circuit: SparkFun NEO-M9N SMA / SparkFun-Batteries
- NEO-M9N symbol: derived from the KiCad `NEO-M8N` symbol
- ICM-45686 pad centers: IPC LGA-14. Pin numbers and axes: TDK DS-000577
