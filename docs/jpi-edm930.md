# JPI EDM-930 post-flight ingest

The EDM-930 in the Bonanza records MAP, RPM, fuel flow, TIT, and the rest of
the engine page. Kingfisher does not see that stream in flight. This tool
parses a download and merges one flight into an existing flight SQLite on
the Pi clock.

It is not a hub device. Nothing here calls `sensors.runOne` or `live.Hub`.

```bash
uv run --project analysis python scripts/analyze_flights.py jpi \
  --jpi analysis/testdata/jpi/FILE.jpi \
  --db ~/kingfisher/flights/FLIGHT.sqlite \
  --flight N \
  --apply
```

Without `--apply` the command prints the fit and does not write. `--db` is
required to align. If the download contains more than one flight, `--apply`
also requires `--flight` (the `$D` flight number, or the 1-based CSV flight).
A suggestion is printed from coarse time overlap. A refused fit exits
non-zero and does not write.

Drop files in `analysis/testdata/jpi/`. See that directory's README.
Extensions: `.jpi` and `.dat` for the USB/EZTrends binary, `.csv` and `.txt`
for a spreadsheet export. Recognition is by content.

## File format

Implemented format: the protocol-2 download used by EDM-830 and by the
EDM-900/930/960 family (JPI's USB stick file, which EZTrends opens).

1. ASCII header. Lines are `$X,...*HH` with CRLF. `HH` is the NMEA XOR of
   the bytes between `$` and `*`. `$D, flight_id, halfsize` is one flight;
   the binary length is `halfsize * 2` bytes, sometimes one byte shorter.
   `$L` ends the header. `$C` carries the model (`930`) and the °F/°C flags.
   `$F` field 1 is the fuel unit (`0` = US gallons). `$P, 2` selects the
   additive checksum.
2. Big-endian flight records. A 29-byte header (flight id, config words,
   lat/lon as int32 counts of 1/6000 degree, interval, packed date and time,
   checksum). Then delta records: two identical mask words, a repeat byte,
   one control byte per set mask bit, sign bytes (mask bits 6 and 7 reuse
   the signs from bits 0 and 3), one delta byte per set control bit, and a
   checksum byte. A valid record sums to 0 mod 256.
3. Running totals start at 240, except the high byte of a 16-bit pair and
   percent power, which start at 0. The first non-zero delta establishes
   the reading. Fuel flow, fuel used, MAP, volts, and amps are tenths when
   the fuel unit is gallons. MAP is inches of mercury. Engine temperatures
   follow the `$C` unit flag (`egt1_f` or `egt1_c`, and the same for CHT,
   TIT, oil temp, CDT, IAT, OAT).

A channel that never leaves the seed is omitted. A stored 0 is a real zero
(for example fuel flow after the first delta lands on 0). `alt_ft` is the
same rule. `lat_deg` / `lon_deg` come from the header plus later deltas of
1/6000 degree. A header position that never moves is stored and is not used
as an alignment track.

Flight split for a binary download: one flight per `$D` record. The monitor
already closes a flight when it stops recording. A non-zero repeat byte is
an in-flight gap: the new sample is delayed by `repeat * interval` seconds
on top of the normal interval. No filler rows are written. With a 6 s
interval the stamps are `t`, `t+6`, and `t+30` when the third record's
repeat is 3.

CSV is a thin reader. Values are display units. Blank cells are null. A gap
longer than 5 minutes, or a change in a `flight` column, starts the next
flight. CSV temperatures without a unit suffix are treated as Fahrenheit.

An unrecognized file is an error. Legacy EDM-700 downloads (no protocol 2)
are refused.

Checked against a real protocol-2 EDM-830 download (public sample, not in
this repo): flight 1196, 216 samples at 1 s, first row RPM 1024, EGT1 1159 °F,
CHT1 125 °F, fuel flow 0.3 gph, 12.7 V. That flight had no MAP and no TIT
bits set. No EDM-930 dump was in the workspace; the synthetic fixture is
model 930 and exercises MAP, TIT, RPM, fuel flow, and a second flight.
Fuel-used tenths follow the published scale. On that 830 file the totalizer
moved 38 counts over a stretch whose integrated fuel flow was about 7.4 gal,
so `fuel_used_gal` is the monitor's counter divided by 10, not a
cross-check that closed. Amps use the same tenths scale when the field
appears; that 830 file never sent it.

## Alignment

The JPI face is not GNSS-disciplined. The model is two parameters:

```text
t_kingfisher = t_jpi + offset_s + drift_s_per_s * (t_jpi - t_ref)
```

`t_ref` is the mean JPI time of the overlap, so the offset is the correction
at that instant. `drift_s_per_s` is seconds of Pi clock per second of JPI
clock, minus one. The report also prints `drift_s_per_h`. The JPI timestamp
is read as UTC. The default search is ±15 minutes and ±3 minutes of
accumulated drift. Widen it with `--window-s` only on purpose.

1. If lat/lon form a track longer than 1.5 km, match it to `gps.lat` /
   `gps.lon`. The score is median horizontal error. Above 750 m, or a flat
   peak, refuses.
2. Otherwise correlate rate of climb, with demeaned altitude at 25% weight
   so a constant altimeter bias does not dominate. Kingfisher source, in
   order: `press_alt.pressure_alt_ft`, `press_alt.indicated_alt_ft`,
   `gps.alt_msl` converted to feet. Refusal is a rate correlation below 0.55
   or a peak that is not distinct inside the window.
3. OAT against `bmp581.static_temp_c` (else another `static_temp_c`, else a
   cabin `temp_c`) is reported, including the offset OAT would have liked.
   It is not in the fit. Thermal lag is a different clock.

## Table

`jpi_edm930.ts_ns` is the aligned Pi `CLOCK_REALTIME` nanosecond, the same
base as every other flight table (`docs/timestamps.md`). `jpi_time` is the
monitor face, `YYYY-MM-DDTHH:MM:SS`, text, no timezone.

The fit is stored once in `metadata`, keys `jpi_edm930_*`:

| Key | Meaning |
|---|---|
| `jpi_edm930_method` | `gps_track` or `altitude_roc` |
| `jpi_edm930_offset_s` | seconds added at `t_ref` |
| `jpi_edm930_drift_s_per_s` | linear drift, seconds per second |
| `jpi_edm930_drift_s_per_h` | the same drift in seconds per hour |
| `jpi_edm930_t_ref_unix` | Unix seconds the offset refers to |
| `jpi_edm930_residual` | median meters, or ft/s RMS of rate of climb |
| `jpi_edm930_residual_unit` | unit of that residual |
| `jpi_edm930_source` | file name |
| `jpi_edm930_flight_id` | `$D` id or CSV flight number |
| `jpi_edm930_oat_pearson` | OAT check at the fitted offset, may be empty |
| `jpi_edm930_oat_source` | Kingfisher temperature column used |
| `jpi_edm930_detail` | which streams were compared |

Running again drops `jpi_edm930` and those keys, then writes them once.
