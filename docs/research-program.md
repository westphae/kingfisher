# Research program

This note is the scientific scope of the Kingfisher recorder on one
turbonormalized Beech Bonanza V35B. It is written from the measurement chain
that the code, config, and analysis tools actually implement. It does not
report flight-test results. No performance model has been fitted, and no
calibration in this document has been validated against a standard.

The airplane in `config.example.json` is named Bonanza V35B. A turbonormalized
V35B holds sea-level manifold pressure to altitude with a turbocharger and a
wastegate. Power is not a function of altitude alone. Any performance number
that does not record how the engine was set is a point on an unknown power
schedule.

## Measurement chain

Every stream is a `live.Sample`: device name, `ts_ns` on the Pi
`CLOCK_REALTIME` clock, and a map of float channels. The store writes `ts_ns`
as produced. It does not restamp at flush. Clock steps and slews are a
separate `clock_offsets` table (`docs/timestamps.md`). Join streams on
`ts_ns`. Use `gps.fix_time_unix_s` only when the question is the GNSS solution
epoch.

| Quantity | Where it is produced | Frame and unit | Rate in this tree | What it is not |
|---|---|---|---|---|
| Cabin accel, gyro | IIO `icm45686-accel`, `icm45686-gyro` | Sensor axes. Accel m/s², gyro rad/s. AHRS converts to g and deg/s | Chip ODR from `sensor_attrs` (cruise notes in `docs/analysis/sensor_noise.md` use 200 Hz chip, boxcar to the stored `sample_hz`; some sessions show 800 Hz ODR). Stored `ts_ns` is kernel realtime, or the **midpoint** of a boxcar window | A calibrated INS. Scale, bias, and mount live in the calibration path (`docs/analysis/calibrate.md`, `docs/analysis/gyro_tco.md`), applied as OFFUSER plus a gyro temperature table, not inside the AHRS integrator |
| Cabin baro | IIO BMP280-class `pressure_pa`, `temp_c` | Cabin air, Pa and °C | Polled or buffered at the device `sample_hz` | Outside air. The cabin is not the static source |
| GPS | gpsd TPV → device `gps` | WGS-84. `gs`/`vs` m/s, `alt_msl` m, `track` deg true | Receiver 10 Hz NAV-PVT; recorded at `gps.rate_hz` (default **5**) | `ts_ns` is receive time. The fix epoch is ~600–700 ms earlier on the M9N path. That lag is the navigation pipeline, and PPS does not remove it |
| Pod static | `bmp581.static_pressure_pa`, `static_temp_c` | Wing pod. Pa, °C | Default **25 Hz**, pressure OSR ×32, IIR 3 (`config.example.json`, `firmware/pod/src/rates.rs`) | Free-air OAT. The element is in the pod. Recovery, solar load, and conduction are unmeasured |
| Pod mag | `mmc5983.mag_*_ut` | Pod axes, µT. Default mount in code flips Z until `compass.sensor_mount_r` is set | Default **20 Hz**, bandwidth 100 Hz | A compass. Hard/soft iron and the pod-to-fuselage rotation are a calibration |
| Pitot | `ms4525.airspeed_dp_pa`, `airspeed_temp_c` | Impact pressure, Pa | Default **10 Hz** | Position-error-corrected airspeed. A zero offset is a separate `airspeed.dp_zero_pa` |
| Pod battery | `bq27441` | Pack voltage, current, SOC, capacity | **1 Hz** | Airplane fuel or electrical load |
| AHRS | `ahrs` roll, pitch, yaw deg; slip, turn rate, g | Whatever frame goflying Simple AHRS assumes after its sensor-to-body rotation | Tick default **20 Hz**. Gyro propagation runs when the GPS **fix epoch** advances, so the effective GPS aiding rate is the recorded GPS rate | Filter time is compute time. `Measurement.TW` is the fix epoch on purpose (`docs/timestamps.md`) |
| Pressure / density altitude | `press_alt` | Feet. Pod static preferred over cabin | **5 Hz** | A panel altimeter trace until the static source and Kollsman setting are the ones in the airplane. Indicated altitude now uses the same standard atmosphere as pressure altitude (29.92 inHg ≡ 1013.25 hPa). Density altitude is the ISA altitude of the measured density. Temperature is the static sensor, not recovery OAT |
| Geomagnetic model | `geo` | WMM field in nT, declination and inclination in degrees, at the GPS lat/lon/alt | **1 Hz** | A measured field. It is the model at the GPS position |
| Compass | `compass.heading_mag_deg`, `heading_true_deg` | Degrees. WMM method solves level yaw (body +Z down) from the current mag against `geo` every tick | Default **10 Hz** | A heading in a bank. The level assumption is explicit in `solveAircraftToEarthMagDown`. Accel alignment is the other method and wants mag and accel in one frame |
| Airspeed | `airspeed.ias_kt`, `tas_kt` | Knots. `ias_kt` is calibrated airspeed from the subsonic airspeed-indicator equation. `tas_kt` is isentropic true airspeed at the static pressure and its temperature | **5 Hz**, optional EMA | Position error, instrument error, or compressibility beyond the standard pitot equation. Below `low_speed_floor` the recorder publishes 0 |
| Howgozit | `hgz_*` tables | Pilot text and numbers, `ts_ns` when the row was added (editable) | Manual | A sensor. It is the only place power setting, fuel, and configuration can be recorded until those instruments exist |
| Host | `system`, `ups` | Pi thermal, load, optional cabin UPS | ~1 Hz | Airplane bus voltage |

Pod time is reconstructed as pod uptime minus `age_us`, plus an EMA offset onto the Pi clock, and clamped to a 10-minute past window so a burst-mode backlog still lands (`internal/pod/pod.go`). Derived rows are stamped at compute time. For a Kalman filter or a position-error fit, use the source tables.

The flight database is one SQLite file per process, WAL, batches flushed on an interval (default 5 s). A failed flush keeps the rows, drops the oldest past 30000 per device, and records the drop count. Inserts are chunked so a full backlog is still a legal statement after the disk recovers.

## What this suite can support

These are questions the recorded channels can speak to, after a procedure and a reduction. They are not results.

**Pressure altitude and a density-altitude proxy.** Pod static pressure through the standard atmosphere is a pressure altitude. With the Kollsman setting stored on `press_alt`, indicated altitude is the same model a mechanical altimeter uses. Density altitude follows from that pressure and the pod temperature. The temperature error becomes a density-altitude error of roughly a hundred feet per degree. Treat `density_alt_ft` as conditional on the temperature channel until a dedicated OAT probe exists.

**Climb angle and rate, not climb performance.** `press_alt.vs_ms` is a smoothed rate of pressure altitude. GPS `vs` is geometric and lags the fix pipeline. Specific excess power along a trajectory is

\[
P_s = \dot h + \frac{V}{g}\dot V
\]

and needs no weight. `analysis/performance.py` reports it from baro rate of climb and true airspeed when both exist. It becomes a Bonanza climb chart only when power (manifold pressure, RPM, mixture or TIT) and weight are known. Without those, a sawtooth climb is one anonymous point.

**Airspeed calibration of this installation.** Impact pressure, static pressure, and GPS groundspeed are the classical inputs to a position-error curve, once wind is removed. The recorder now computes CAS and TAS with the compressible pitot equation; the remaining error is position error, zero, leaks, and the temperature used for TAS. A trailing cone or a certified pitot-static ship is the reference that separates static-source error from everything else. GPS geometric altitude versus pressure altitude is a useful static-system check and is not itself a pressure standard.

**Magnetic heading quality, on the ground and in level flight.** Taxi at 2–40 kt gives GPS track as a heading reference if the airplane is tracking the gear and the wind is small. The WMM comparison (`compass` field channels versus `geo`) is the in-flight residual of hard/soft iron after the level-yaw align. Banked heading is outside the current solver. Declination is the model at the GPS fix, which is the right model input and the wrong input if the GPS position is stale.

**Wind, in steady level flight, as geometry.** Wind is ground velocity minus air velocity. That needs TAS, true heading, and GPS track at one `ts_ns`. Heading quality and the OAT-in-TAS error land directly in the wind. A four-leg box, or a day with a known calm, is the check. A single cruise leg does not identify wind and TAS error separately.

**Stall, glide, and specific range.** Stall speed and glide ratio need airspeed, weight, and configuration (flaps, gear, possibly bank). Specific range needs fuel flow. None of the configuration or fuel channels exist. Howgozit can hold a pilot entry of "approach flaps, 18 gal/side" at a `ts_ns`. That supports a handful of documented points. It does not support a drag polar.

**Weight and balance.** Not observable. Empty weight, moment, and fuel quantity have to come from the airplane's records and a fuel log.

**Engine and turbocharger.** Not observable. Manifold pressure, RPM, fuel flow, TIT, EGT, and CHT are absent. For a turbonormalized airplane this is the hole that blocks a performance handbook. Full-throttle climbs change compressor work and fuel flow with altitude; the airframe sensors cannot see that.

## Calibrations that flights can identify

Identifiable with the sensors already on the airplane, if the procedure is flown:

| Calibration | Observables already stored | Procedure the data do not replace |
|---|---|---|
| Pitot zero | `ms4525.airspeed_dp_pa` while still; the cockpit zero averages ~15 s | Same-day, wind off the probe, recorded `dp_zero_pa` |
| Airspeed position error | CAS from ΔP, GPS groundspeed, track, heading or a wind box, pressure altitude | Stabilized points, several speeds, wind removed. `analysis/performance.py` assembles the medians; it does not fit a PEC curve |
| Static source consistency | `bmp581` pressure altitude vs GPS `alt_msl` vs cabin baro | A tower fly-by or a known-elevation hover/taxi is the external check. GPS altitude is not a static-pressure standard |
| Compass / WMM residual | `mmc5983`, `compass`, `geo`, GPS track in the taxi band | A compass swing or a set of taxi headings. Level-flight residual is meaningful; banked heading is not, until attitude is in the solver |
| Accel scale and gyro temperature | Six-face and still captures, `calibration` JSON, OFFUSER | Already a bench path (`docs/analysis/calibrate.md`, `gyro_tco.md`). Flight data check \|a\| in steady 1 g and gyro drift in a long straight leg |
| OAT, weakly | Pod `static_temp_c` or cabin `temp_c` against TAS−GPS in a known calm | This comparison confounds temperature, position error, and wind. It can show that temperature is the limiter. It cannot assign a recovery factor by itself |
| Altimeter setting | `press_alt.kollsman_inhg` and indicated vs pressure altitude | The setting has to be the one in the Kollsman window that day |

Not identifiable without new instrumentation or an external standard: manifold pressure and RPM (so the power term), fuel flow, TIT/EGT/CHT, wastegate position, flap and gear position, fuel quantity, angle of attack, control-surface deflection, and a certified pitot-static or trailing-cone truth.

## Missing sensors, by leverage per installation pain

Ranked for this airplane, not for a generic testbed.

1. **Manifold pressure and RPM.** One hose to the deck pressure and a tach tap. Unlocks climb and cruise performance against a real power setting, which is the difference between "the airplane climbed" and "the airplane climbed at 29 inHg and 2300 RPM." For a turbonormalized engine the manifold-pressure altitude where the turbo runs out of wastegate is the performance knee. Nothing else in the suite sees it.

2. **Fuel flow.** A transducer in the fuel line, or a disciplined totalizer dump aligned to `ts_ns`. Unlocks specific range and the fuel term in weight. Howgozit fuel remarks are the interim and they are sparse.

3. **TIT, and EGT/CHT if the instrument is already in the panel.** A turbonormalized mixture schedule is TIT-limited. Without TIT, cruise points cannot be labeled rich-of-peak or lean-of-peak, and the range calculation is an assumption.

4. **Dedicated OAT**, shielded, in free stream, not the BMP581 die and not the MS4525 temperature. Unlocks an honest density altitude and the temperature half of TAS. Install after the pitot zero is trustworthy; the analysis plan already says the same thing. Do it before publishing any TAS or specific-range number.

5. **Throttle and wastegate position**, even as a coarse potentiometer or a discrete "full forward". Cheaper than it sounds for labeling points. Less leverage than MP/RPM, which already integrate those positions into the quantity that performance cares about.

6. **Flap and gear discretes.** A couple of switches. Unlocks configuration-specific stall and approach speeds. High leverage for handling points, low pain.

7. **Fuel quantity** (or a fuel-used integration of item 2 plus a departure dipstick in Howgozit). Unlocks weight through the flight and, with the loading sheet, a CG track.

8. **Angle of attack.** A vane or a second pressure port, after the pitot-static installation is believed. Unlocks stall margin and a lift curve. Premature until CAS is within a knot or two of a reference.

9. **Certified pitot-static reference or a trailing cone**, borrowed for a dedicated flight. This is the truth reference for position error and static error. It is not a permanent install. One flight with it is worth more than a year of GPS-only inference.

10. **Control positions** (stick, yoke, rudder). Required for stability-derivative identification. High installation pain, and the airframe questions above do not need them. Defer until a handling-qualities program exists.

Audio and ATC are already partially covered by Howgozit (frequency and facility rows with `ts_ns`). A cockpit audio recording would time-align radio calls; it does not change the aerodynamic state.

## Characterization versus identification

**Engineering characterization of this airplane** is in reach. The product is a set of documented points for this serial number: position-error curve, static-system residual against a known elevation, compass deviation on the ground, and climb rate at a stated weight and a stated power setting once MP and RPM exist. The standard of evidence is a procedure, repeats, and an uncertainty built from sensor noise and the wind residual. `docs/analysis/` is already aimed here: motion windows, noise, accel and gyro calibration, then a first-order check of the derived channels.

**Publishable flight-dynamics identification** is not in reach. That work estimates stability and control derivatives from maneuver time histories. The minimum instrumentation is air-relative speed and flow angles, angular rates, attitude, and control deflections, time-aligned to a few milliseconds, with a turbulence or maneuver input that is known. This recorder has rates and a simple attitude filter, GPS groundspeed with a 600–700 ms solution lag, and no flow angles or control positions. The AHRS is goflying's Simple AHRS, which rotates accel toward a GPS-derived specific force and blends a gyro quaternion. It is a display attitude. It is not a research INS, and its gyro update is tied to GPS fix epochs. Boxcar-averaged IMU samples are the right archive for the stored rate; they are a poor input to a derivative estimate at the short-period frequency.

Do not describe a future parameter-identification paper as something the current flights already contain.

## First study worth flying

One flight, one question: **the position error of this pitot-static installation, and a density-altitude context for the points.**

Conditions:

- Weight and balance from the loading sheet and a fuel stick, written into the flight notes or a Howgozit row at engine start. The recorder cannot weigh the airplane.
- Kollsman setting in the cockpit matched to `press_alt` (the UI writes it).
- Pitot zero on the ground the same day, wind not down the probe. The zero path is `SamplePitotDpAverage`, 15 s, at least 50 samples.
- Calm morning, or a four-leg box at one altitude and one indicated speed so the wind can be solved and removed. A single heading cannot separate a 3 kt wind from a 3 kt position error.
- Three stabilized level speeds (for example slow cruise, normal cruise, and a faster point inside the green arc), one to two minutes each, configuration clean. Record the power setting in Howgozit even though MP is not instrumented: throttle position in words, RPM from the tach if the pilot reads it, mixture full rich or a stated TIT if a gauge exists. Those words keep the points from being misread as a power sweep.
- Optional: one timed climb, full throttle, RPM read from the tach and written down, from a stated altitude to another. Report rate of climb and specific excess power as observed at that anonymous power setting. Do not plot it on a handbook chart.

Truth references for this study:

- GPS groundspeed and track, joined on `ts_ns`, with the fix-epoch lag left as a constant and not "corrected" into the IMU.
- Pressure altitude from pod static pressure.
- A known airport elevation for one ground static check.
- No claim of a trailing-cone accuracy. State the residual as "CAS versus wind-corrected GPS groundspeed."

Reduction: `analysis/performance.py` on that flight database. It recomputes CAS and TAS from ΔP and BMP581 pressure and temperature, so a database recorded before the live equation change is not stuck with the old incompressible column. It will say which tables were absent. It does not fit a curve and it does not emit a handbook number.

Success for the study is a plotted position-error residual with a stated wind method and a temperature caveat, plus a written list of the power and weight assumptions. Failure is a smooth curve with no wind check.

## Priority stack

Do these in order. Later items are real; they are not next.

1. **Fly the position-error flight above**, after a same-day pitot zero. Confirm in that recording that `icm45686-accel` and `icm45686-gyro` both appear inside `ahrs` (the derive path now merges them; before that, map iteration could pick only one). Confirm `compass.heading_true_deg` tracks GPS track in level flight and is ignored in a bank. This is the gate on the air-data and attitude work already in the tree.

2. **OAT probe design, not yet the install**, using the residual from that flight. If TAS and wind-corrected GPS disagree by a temperature-shaped amount, build the probe. If they disagree by a speed-shaped amount, the static source and the PEC come first.

3. **Manifold pressure and RPM.** Highest leverage for this engine. Do not start a climb-performance campaign before they are in the database on the same `ts_ns` clock. A Howgozit column for RPM is acceptable for the first study and is not a substitute.

4. **Fuel flow**, then a departure fuel entry so weight can be integrated. Specific range waits on this and on OAT.

5. **WMM heading in a bank.** Share one aircraft-to-earth rotation between AHRS and compass, with an agreed FRD/NED sign. The level solver is enough for the position-error flight. It is not enough for a turn.

6. **ICM-45686 and ICM-20948 hardware FIFOs** (sibling driver trees). The stored stream is already boxcar-averaged and midpoint-stamped. FIFO matters when a handling program needs the unaveraged rate. It does not unblock items 1–4.

7. **Pod burst-mode endurance.** The power protocol is implemented and documented (`docs/pod-power.md`). Longer than a 60 s ring is an endurance choice. An AP outage longer than the ring drops samples by design. Leave it until a flight actually exhausts the pack or the ring.

8. **Hello-carried chip attrs on the wire** (`internal/pod/TODO.md`). Reproducibility of OSR and bandwidth. The Pi already logs the attrs it commanded. Do this when a calibration depends on knowing the pod's register state rather than the Pi's cache.

9. **Angle of attack, trailing cone, control positions.** Handling-qualities and derivative work. Explicitly deferred until CAS is trusted and the performance points above exist.

Clock discipline (chrony, PPS, the nudge and `clock_offsets` machinery) stays in service. It is not a research task unless a flight opens with `clock_startup_fallback` set. The GPS receive-versus-fix gap stays in the database as two columns. It is not a bug to remove.

## What the equations are, and what they are not

Live `ias_kt` is

\[
V_c = a_0 \sqrt{5\left[\left(\frac{q_c}{P_0}+1\right)^{2/7}-1\right]}
\]

with sea-level ISA \(a_0\) and \(P_0\). Live `tas_kt` is the same expression at the measured static pressure and the measured temperature. The incompressible \(\sqrt{2q/\rho_0}\) reads high by more than a knot at a 180 kt sea-level calibration point; that is why it was replaced. `indicated_alt_ft` inverts the same feet-form standard atmosphere as `goflying` `bmp280.CalcAltitude` instead of adding 1000 ft per inHg. `density_alt_ft` is the ISA altitude of \(P/(RT)\), troposphere then the isothermal layer, not 120 ft per degree.

`analysis/performance.py` repeats those equations for offline files and adds specific excess power and a wind triangle. Unit tests check the algebra on synthetic rows. They do not check an airplane.
