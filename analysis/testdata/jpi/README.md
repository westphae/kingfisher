# JPI EDM-930 dumps

Drop raw downloads here. They are gitignored. Do not commit a real flight log.

Preferred: the USB-stick file the EDM writes, or the same file after EZTrends
"Move and Plot Data from Memory Stick". Names look like `U20240601.JPI`.
Extensions read: `.jpi`, `.dat` (same binary family).

Fallback: a CSV export. EZTrends can save one flight as `.xls`, then Excel
"CSV (MS-DOS)". EzSave writes CSV directly. Extensions read: `.csv`, `.txt`.

The tool decides by content, not by the extension. A binary download starts
with a `$U` header. A CSV needs a header row with a timestamp and at least
one of RPM, MAP, EGT, or fuel flow. Anything else is an error, not a guess.

```bash
uv run --project analysis python scripts/analyze_flights.py jpi \
  --jpi analysis/testdata/jpi/U20240601.JPI \
  --db ~/kingfisher/flights/FLIGHT.sqlite \
  --flight 12
# add --apply to write jpi_edm930
```

Set the EDM clock to the same time base as the Pi (UTC), within a few
minutes. The search is ±15 minutes by default. A timezone-sized error is
refused.

`synthetic_edm930.jpi` and `synthetic_edm930.csv` are tiny authored fixtures,
not flight data. The format is documented in `docs/jpi-edm930.md`.
