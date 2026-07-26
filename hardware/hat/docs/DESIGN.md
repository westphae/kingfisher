# Kingfisher Sensor HAT — design notes (rev 0.2)

## Goals

Replace the rear-bay SparkFun NEO-M9N + ICM-45686 breakouts (and ribbon) with a
single board that plugs onto the **extra-tall GPIO header** protruding through
the official M.2 HAT+. Lower profile than the IDC ribbon connector.

CO sensing is **out of scope** (separate cabin CO monitor). Pod SHT45 humidity
is a later pod change, not on this HAT.

## Stack fit

```
X1200 UPS → Pi 5 → Active Cooler → M.2 HAT+ → [extended GPIO pins +9 mm]
                                              → this HAT (female socket on bottom)
```

Constraints from `enclosures/x1200_m2hat_stack.md`:

- Pins above M.2 HAT top: **9.0 mm**
- Current IDC ribbon stack: **~19.4 mm** above HAT — we must stay under that
- Corner M2.5 bolts currently start at M.2 HAT top; HAT may need longer bolts
  or light pin-only seating for v1 prototypes
- **CR2032 holder height** (Keystone 3002 ~5–6 mm above PCB) must clear the
  case lid — verify in cardboard/3D mockup before fab

**Action before fab:** 3D-print or cardboard blank with chosen female header
and confirm engagement + lid clearance (especially BT1).

## Electrical map (matches `pinouts.md`)

| Pi header | Net | Destination |
|-----------|-----|-------------|
| 1 | +3V3 | ICM, BMP581, RV-3028 |
| 2, 4 | +5V | AP2112K VIN |
| 3 | SDA | ICM + BMP581 + RV-3028 |
| 5 | SCL | ICM + BMP581 + RV-3028 |
| 6, 9, … | GND | common |
| 8 (GPIO14 TXD) | UART_TX | NEO RXD |
| 10 (GPIO15 RXD) | UART_RX | NEO TXD |
| 11 (GPIO17) | IMU_INT | ICM INT1 (active-low in DT) |
| 12 (GPIO18) | GPS_PPS | NEO TIMEPULSE |

All other GPIO pins are passed through the 40-pin socket (X1200 fuel gauge
`0x36` and PLD GPIO6 remain available on the bus).

### I²C addresses (cabin bus 1)

| Addr | Device | Notes |
|------|--------|-------|
| `0x36` | X1200 MAX17040 | existing UPS |
| `0x47` | BMP581 | SDO=1 |
| `0x52` | **RV-3028-C7** | RTC (not DS3231) |
| `0x68` | ICM-45686 | AP_AD0=0 |

**Why not DS3231?** Stock DS3231 is almost always at **`0x68`**, which collides
with the ICM. Moving the ICM to `0x69` (AP_AD0=1) is possible but forces a
device-tree / software change for no gain. Micro Crystal **RV-3028-C7** is a
common low-power I²C RTC at **`0x52`**, has a dedicated `VBACKUP` pin, and is
already in the KiCad symbol library with a SON-8 footprint.

DTO overlay (software follow-up): `dtoverlay=i2c-rtc,rv3028` (or equivalent
for the Pi 5 I²C-1 bus). Gives wall-clock before GPS/chrony lock; chrony/PPS
remain the in-flight truth once locked.

### Sensor strap notes

- **ICM-45686:** AP_CS → +3V3 (I²C mode); AP_AD0 → GND (`0x68`); 100 nF on VDD and VDDIO.
- **BMP581:** CSB → +3V3 (I²C); SDO → +3V3 (`0x47`); INT → GND (unused); 100 nF on VDD/VDDIO. No vias under package (Bosch).
- **RV-3028-C7:** VDD → +3V3; VBACKUP → **VBAT** (direct, no diode — RTC expects the cell); SCL/SDA on bus; INT/CLKOUT/EVI unused (NC or test pad).
- **NEO-M9N:** VCC from +3V3_GPS; **V_BCKP from diode-OR** (not tied to VCC); D_SEL open (UART+I2C); TIMEPULSE → PPS; RF_IN → U.FL. Follow u-blox integration manual for RF keepout / GND.

## Power

- Digital sensors + RTC on Pi **+3V3**
- GPS on dedicated **AP2112K-3.3** from Pi **+5V** (same approach as SparkFun M9N breakout; avoids starving the Pi 3V3 rail)
- LDO input/output caps per AP2112 datasheet (1 µF class)

## Backup power (CR2032)

### Cell choice: CR2032 (not CR1220)

| | CR2032 | CR1220 |
|---|--------|--------|
| Typical capacity | ~220 mAh | ~35–40 mAh |
| Holder | Keystone **3002** (common HAT part) | smaller, less common |
| Height | taller (~5–6 mm holder) | lower profile |
| Retention | years at µA loads | months–~1 year depending on GPS backup draw |

**Pick CR2032:** GPS `V_BCKP` + RTC backup are both microamp-class, but we want
multi-year cold-cabin / hangar storage without babysitting a tiny cell. Height
is the trade-off — check lid clearance. Replaceable holder (not soldered tab
cell) so the pilot can swap without desoldering.

### Topology (SparkFun-style, primary cell)

```
                 BAT54 D1
  +3V3_GPS ──|>|──┐
                   ├── V_BCKP ──► NEO-M9N pin V_BCKP
  VBAT ──────|>|──┘
         BAT54 D2

  VBAT ──────────────► RV-3028 VBACKUP
  BT1 CR2032 (+) = VBAT, (−) = GND
```

- **Do not** tie NEO `V_BCKP` to `VCC` — that loses ephemeris/RTC state on every
  power-off (the failure mode of a naive breakout clone).
- **Do not** provide a charge path into the CR2032 (primary LiMnO₂). Schottky
  OR from `+3V3_GPS` only *supplies* `V_BCKP` when the rail is up; when the rail
  is down, the cell feeds `V_BCKP` through D2. RTC `VBACKUP` is fed from `VBAT`
  directly (chip isolates internally).
- SparkFun’s NEO-M9N board uses a similar diode-OR (cell/supercap vs VCC). We
  prefer a **replaceable cell** over a supercap for hangar-week retention
  without a charge circuit.

### Current draw / retention (order-of-magnitude)

| Load | Typical | Source |
|------|---------|--------|
| NEO-M9N `V_BCKP` | ~15 µA (backup mode) | u-blox NEO-M9N datasheet (backup supply) |
| RV-3028 timekeeping on `VBACKUP` | ~45 nA | Micro Crystal datasheet |
| Combined (power off) | **~15 µA** | dominated by GPS |

CR2032 ~220 mAh → **~220e3 / 15 ≈ 14 000 h ≈ 1.6 years** continuous backup at
that draw (ideal; self-discharge and cold reduce this). In practice the aircraft
is powered often enough that calendar life / self-discharge dominate — still
plan on **annual cell check** at annual / condition inspection.

## PCB

- Outline: KiCad Raspberry Pi HAT template
- Copper: **4 layers** (RF / return for GPS)
- J1: pin socket on **bottom** copper (already in template)
- Major parts placed in rev 0.1; **routing TBD**; place BT1 / U5 / D1–D2 next

Suggested placement:

| Part | Approx. location |
|------|------------------|
| J1 | GPIO edge (template) |
| U1 ICM | near mid-left |
| U2 BMP581 | near mid-right |
| U4 NEO | center |
| J2 U.FL | toward outer edge for pigtail |
| U3 LDO | near GPS |
| U5 RTC | away from RF / near I²C |
| BT1 CR2032 | board edge, lid-accessible, clear of antenna |

Silk: mark ICM +X/+Y/+Z to match AHRS convention (verify against package pin-1 and TDK figure before fab).

## BOM notes (backup / RTC deltas)

| Ref | Value | Footprint / notes |
|-----|-------|-------------------|
| U5 | RV-3028-C7 | `Package_SON:MicroCrystal_C7_SON-8_1.5x3.2mm_P0.9mm` |
| BT1 | CR2032 (cell) | holder `Battery:BatteryHolder_Keystone_3002_1x2032` |
| D1, D2 | BAT54 | SOD-123 (or BAT54C dual if layout prefers) |
| C8 | 100 nF | RTC VDD decouple |

Do **not** populate a charging resistor/IC for BT1.

## What else along these lines? (resilience shortlist)

Given **X1200 UPS already present**, **chrony + PPS already in software**, and
tight stack/lid height, only add HAT features that earn their keep.

### Recommend (ship or leave pads)

| Item | Verdict | Why |
|------|---------|-----|
| **I²C pull-ups (2×2.2–4.7 kΩ to +3V3)** | **Yes if bus needs them** | Pi usually has onboard pull-ups; with X1200 + HAT + ribbon history, measure SCL/SDA rise time. Prefer **DNP** footprints so we can populate only if the stack is flaky. |
| **Test points** | **Yes** | TP for `+3V3`, `+3V3_GPS`, `VBAT`, `V_BCKP`, SDA, SCL, UART_TX/RX, PPS, IMU_INT. Cheap, saves debug time in the aircraft. |
| **Power-good LED (Pi +3V3 or GPS 3V3)** | **One LED, optional** | Single green on `+3V3_GPS` (or +3V3) with ~1–2 mA resistor — confirms HAT power without a scope. Skip per-rail LED forest. |
| **SAFE_BOOT / nRPI_BOOT test pad** | **Pad only** | Handy if the stack bricks boot; no connector needed — just a labeled pad to GND. |
| **Series R on UART (22–100 Ω)** | **Optional DNP** | Helps edge-rate / EMI if the GPS UART rings; start without, add footprints if layout allows. |
| **HAT ID EEPROM (CAT24C256)** | **Keep template footprint, DNP OK** | Template already has it. Useful for `hat_map` / inventory; not required for kingfisher software today. |

### Skip

| Item | Why skip |
|------|----------|
| **Second hardware watchdog** | Pi + systemd + X1200 cover brownout/reset; another WDT on the HAT is complexity without a clear failure mode. |
| **IMU / GPS “activity” LEDs** | Clutter, current, and no pilot value in a closed case; use the cockpit UI. |
| **Supercap on V_BCKP** | CR2032 diode-OR already covers hangar retention; supercap needs charge path and dies in days–weeks. |
| **Rechargeable ML-type cell** | Charge circuitry + aviation preference for primary coin cells you can swap dry. |
| **Separate RTC coin cell + GPS coin cell** | One CR2032 is enough at ~15 µA; two holders burn lid space. |
| **Extra baro / humidity on HAT** | BMP581 is enough for cabin pressure altitude; humidity deferred to pod. |
| **CO sensor** | Cabin CO monitor already exists. |

## Software impact (after hardware)

- ICM: existing `icm45686` overlay / kingfisher path — unchanged at `0x68`
- GPS: existing `uart0-pi5` + `pps-gpio,gpiopin=18` — unchanged; backup battery improves cold-start TTFF after power cycles
- BMP581 cabin: **new** — Linux IIO or userspace reader; altitude derive already has cabin pressure fallback
- RTC: **new** — `i2c-rtc` / `rv3028` overlay so the Pi has time before GPS lock; chrony remains primary when locked

## Fab checklist (later)

- [ ] Female header part number chosen for 9 mm pin engagement
- [ ] Lid clearance for Keystone 3002 + cell
- [ ] ERC / DRC clean (tidy label–pin wires in eeschema)
- [ ] u-blox RF review (short RF_IN, continuous GND, no cutouts under module)
- [ ] Place BT1, U5, D1/D2, test points; route
- [ ] JLCPCB: ICM + BMP581 + RTC + passives SMT; NEO module availability check
- [ ] Order 5 boards

## Schematic toolchain note

`scripts/build_sch.py` embeds symbols into `lib_symbols`. KiCad unit children
must keep the **short** name (`Name_0_1`), not `Lib:Name_0_1` — the latter
makes `kicad-cli` fail with “Failed to load schematic”. Regenerate with:

```bash
python3 hardware/hat/scripts/build_sch.py
kicad-cli sch export netlist hardware/hat/kingfisher-hat.kicad_sch
```
