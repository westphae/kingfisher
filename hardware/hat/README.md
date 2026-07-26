# Kingfisher Sensor HAT (rev 0.2)

Custom Raspberry Pi HAT that consolidates the cabin sensors currently on SparkFun /
breakout boards + ribbon cable:

| Ref | Part | Role |
|-----|------|------|
| U1 | ICM-45686 | Cabin IMU (I²C `0x68`, INT → GPIO17) |
| U2 | BMP581 | Cabin baro / temp (I²C `0x47`) |
| U3 | AP2112K-3.3 | 5 V → 3.3 V for GPS |
| U4 | NEO-M9N | GNSS (UART0 + PPS → GPIO18); `V_BCKP` diode-OR’d |
| U5 | RV-3028-C7 | I²C RTC (`0x52`), `VBACKUP` ← CR2032 |
| BT1 | CR2032 + Keystone 3002 | Replaceable backup for RTC + GPS |
| D1, D2 | BAT54 | Schottky OR: `+3V3_GPS` / `VBAT` → `V_BCKP` |
| J1 | 2×20 pin socket | Mates **extended GPIO pins above the M.2 HAT+** (bottom side) |
| J2 | U.FL | Antenna → existing SMA bulkhead pigtail |

Form factor: official Pi HAT outline (65 × 56.5 mm, M2.5 holes 58 × 49).

**Why RV-3028 not DS3231?** DS3231 sits on `0x68` and would collide with the ICM.
**Why CR2032 not CR1220?** ~5× capacity for multi-year GPS/`V_BCKP` + RTC backup;
Keystone 3002 is a common replaceable HAT holder (check lid height).

See [docs/DESIGN.md](docs/DESIGN.md) for the backup topology, I²C map, retention
estimates, and the “what else” resilience shortlist (test points / DNP pull-ups
recommended; second watchdog / LED forest skipped).

## Open in KiCad

Requires **KiCad 9** (project generated against 9.0.2).

```bash
kicad hardware/hat/kingfisher-hat.kicad_pro
```

Project-local libraries:

- `libs/Kingfisher.kicad_sym` — ICM-45686, BMP581, NEO-M9N
- `libs/Kingfisher.pretty` — BMP581 land pattern (adapted from SparkFun Qwiic BMP581, CC-BY-SA 4.0)

Regenerate libraries / schematic scaffolding (stdlib only — **no uv project**):

```bash
python3 hardware/hat/scripts/build_libs.py
python3 hardware/hat/scripts/build_sch.py   # overwrites schematic
kicad-cli sch export netlist hardware/hat/kingfisher-hat.kicad_sch
```

Any future `pcbnew` automation must use **system** Python with apt KiCad
(`python3-pcbnew`), not a uv venv — the PCB API is not a PyPI package.

## Status (rev 0.2)

Done:

- HAT mechanical from KiCad Raspberry Pi HAT template (outline, holes, bottom GPIO socket)
- Custom symbols + BMP581 footprint
- **Loadable** schematic rev 0.2 (ICM, BMP581, GPS, LDO, U.FL, RV-3028, CR2032, diode-OR)
  — netlist exports 18 components; open in eeschema to tidy wire/label geometry
- PCB: 4-layer stack selected; major footprints placed (not yet routed); BT1/U5/D1–D2 placement TBD
- Design notes for RTC address, GPS backup, and resilience add-ons

Not done yet:

1. Schematic pin–label wiring cleanup + ERC green
2. Place BT1 / U5 / diodes / test points; full routing
3. Cardboard / 3D mockup (pin engagement + CR2032 lid clearance)
4. JLCPCB BOM / CPL export
5. Software: cabin BMP581 reader + `i2c-rtc` for RV-3028

## Attribution

- Board outline / GPIO placement: KiCad `RaspberryPi-HAT` template
- BMP581 pad geometry: adapted from [SparkFun Qwiic Pressure Sensor BMP581](https://github.com/sparkfun/SparkFun_Qwiic_Pressure_Sensor_BMP581) hardware (CC-BY-SA 4.0)
- NEO-M9N symbol derived from KiCad library `NEO-M8N` (same NEO pinout family)
- GPS backup idea: SparkFun NEO-M9N breakout (diode-OR backup supply; we use replaceable CR2032)
