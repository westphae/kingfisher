# AGENTS.md

Operational notes for Cursor agents (including Grok) working in this repo.
`CLAUDE.md` is the architecture guide. This file is the resume point for
JPI EDM-930 work on the aircraft Pi. The cloud transcript that wrote the
ingest is not on the Pi.

## Before touching JPI code

Read these first:

- `CLAUDE.md`
- `docs/jpi-edm930.md` (format, alignment, and the `jpi_edm930` table)
- `analysis/testdata/jpi/README.md` (where dumps go, and what not to commit)

## Code and entry point

Code lives in `analysis/jpi/` (`parse.py`, `align.py`, `store.py`, `cli.py`).
Entry point:

```bash
uv run --project analysis python scripts/analyze_flights.py jpi \
  --jpi PATH/TO/FILE.JPI \
  --db PATH/TO/FLIGHT.sqlite \
  --flight N
```

Dry-run is the default. `--apply` writes `jpi_edm930` and the
`jpi_edm930_*` metadata keys. More than one flight in the download
requires `--flight` before `--apply`. A refused match exits non-zero and
writes nothing.

## This installation

The pilot's EDM-930 download has no altitude and no second TIT. Alignment
is GPS ground track only. Altitude rate-of-climb is the fallback for a
download that actually carries `alt_ft`; it will not fire on this file.

Model:

```text
t_kingfisher = t_jpi + offset_s + drift_s_per_s * (t_jpi - t_ref)
```

Search ±15 min of offset and ±3 min of accumulated drift. Refusal for the
track fit: path longer than 1.5 km, at least 20 fixes, median error
≤ 750 m, and a distinct peak. OAT is a check, not a fit input. Score fix
epochs only. Fixes arrive every ~10–14 s; the position quantum is
~1/6000 degree. Held rows between GPS sentences are not extra measurements.

## Real download (not in git)

`U260921.JPI` was parsed and left untracked. `analysis/testdata/jpi/`
gitignores real `.jpi` / `.dat` dumps and flight SQLite files. The pilot
copies the file onto the Pi. Do not commit it, and do not commit a flight
database.

What that parse showed:

- Protocol-2 model 930, 12 flights, 2 s interval, about 20,000 samples.
- Channels present: RPM, MAP, fuel flow, fuel used, EGT1–6, CHT1–6, TIT1,
  oil temp, oil pressure, OAT °C, CDT, IAT, volts, amps, percent power,
  lat/lon. No `alt_ft`, no `tit2`.
- Eight flights have a usable track (about 60–950 km). Four do not: a
  frozen header, unsettled GPS, or a 2 km hop with two fixes. Those four
  must be refused.
- GPS-init marks (±100 counts on both axes at once) and impossible steps
  (>250 kt, including a 0xFF latitude glitch of about 1,200 km) are
  dropped. Do not put those samples back.
- `fuel_used_gal` is the monitor counter / 10. It tracked integrated fuel
  flow within about a gallon except where the totalizer resets.

Known leftover: two flights show a sustained RPM band above 2700 after a
high-byte step. The scale formula was left unchanged because the other
flights look normal. Investigate on the Pi against the real file before
changing it.

## Next task on the Pi

Dry-run the real download against the matching flight DB. `--apply` only
if the residual passes the gates above.

```bash
python3 -m unittest analysis.test_jpi_parse analysis.test_jpi_align analysis.test_jpi_store
```

Optional extra coverage: `JPI_SAMPLE=/path/to/file.jpi`.

## Constraints

Sibling Go replaces in `go.mod` (`../go-iio`, `../goflying`, `../geomag`,
`../magkal`) must not be dropped. If imports fail, fix the sibling
checkout.

JPI is post-flight only. Do not wire it through `live.Hub` or
`sensors.runOne`.
