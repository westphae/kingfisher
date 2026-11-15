# Engine review

Post-flight scorecard for the TAT turbonormalized IO-550-B in N456T
(AFMS-550 Rev K, STC SA5223NM), from a merged `jpi_edm930` table or from a
raw EDM-930 download. It does not subscribe to `live.Hub`. Boost-pump
position, cowl flaps, and mixture are not logged; the review infers them
from fuel flow, EGT, MAP, and RPM.

```bash
uv run --project analysis python scripts/analyze_flights.py engine --db ~/kingfisher/flights/FLIGHT.db
uv run --project analysis python scripts/analyze_flights.py engine --all
uv run --project analysis python scripts/analyze_flights.py engine --jpi ~/kingfisher/engine/raw/U261112.JPI
```

`--all` reviews every flight DB that has `jpi_edm930` rows, then any raw
`U*` download whose flight does not already overlap a reviewed DB (the
Jan–May 2026 downloads, which have no Kingfisher database). GPS and IAS
checks are skipped when those columns are absent. Results are cached under
`analysis-cache/engine/` and reused until the DB bytes or the limits file
change.

The browser (`serve`, port 8765) loads `GET /api/sessions/<file>/engine`
when a session opens and draws the scorecard under the plot. Clicking an
event or a phase zooms the chart to that window and adds the series the
event names. **All flights** calls `GET /api/engine/summary`.

## What is measured

Phases come from RPM, MAP, fuel flow, groundspeed, and altitude:
taxi, mag check, takeoff, climb, level-off, cruise, descent, landing,
shutdown. A high-power climb stays a climb through a brief level bump.
Without altitude, a descent flown at cruise power stays cruise and the
descent check says so.

A session can contain more than one EDM recording. Phase logic, rolling
windows, and boost/vapor steps run inside each capture. A gap is not
filled in, and a second engine start gets its own taxi and takeoff. The
flight list shows `JPI`, `JPI ×N`, `no JPI`, or `JPI refused`.

Each item is pass, caution, fail, or skip. Skip is not a pass. The flight
fails if any item fails. The observed value and the limit are both on the
row. Events carry a timestamp so the chart can zoom to them.

Defaults are AFMS Rev K where the supplement states a number:

- Takeoff fuel flow 35 gph below 80°F OAT, 34 at 81–90°F, 33 above 90°F.
- Takeoff TIT 1250–1320°F. Climb TIT 1260–1310°F. TIT redline 1650°F.
- Full-throttle MAP 29.6 in Hg. With the EDM as primary, yellow runs to
  32.0 and red is 32.0. 30–32 is accepted briefly with oil under 170°F.
- Climb 2700 RPM (2650 is the noise-abatement set), wide-open throttle,
  full rich about 35 gph, hottest CHT under 380°F. 410°F is the review's
  fail below the 460°F redline. Climb IAS 115–120 KIAS when IAS is usable.
- Mag check at 1700 RPM, drop at most 150 RPM, the two sides within 50 RPM.
- Cruise wide open at 2500 RPM / 15.5–17.5 gph or 2300 RPM / 14.0–16.0 gph,
  never below 2300. Lean of peak at least 60°F, ideally 50–100. The red box
  is between 50°F lean of peak and 125°F rich of peak above 65% power.
  Rich of peak is limited to 25 in Hg and 2500 RPM and at least 125°F rich.
  Lean-of-peak horsepower is fuel flow times 14.9.
- Descent keeps CHT at or above 240°F. Approach fuel flow is 1–2 gph above
  cruise. After a high-power taxi, idle 10–20 seconds before shutdown.
- Oil pressure red below 10 and above 100, yellow 10–30, green to 60.
  Oil temperature red 240°F, green 100–240. The wastegate note is about
  45 psi to hold rated MAP up high.

These are review judgments, not lines in the supplement. Eric accepted the
current defaults: cold-oil MAP under 170°F for at most 90 seconds, CHT
fail at 410°F, a missing mag check as a caution, taxi lean at 6 gph, and
vapor residual at 0.35 gph. The other inferred sizes stay as coded: boost
steps from the 11/11 ground test (LO about +0.5 gph and +20°F EGT, HI
about +3.5 gph, +60°F EGT, +0.2 in Hg) and the LO-lowers-fuel-flow anomaly.
LO is expected above 5,000 ft density altitude and HI above 10,000. Density
altitude is estimated from the altitude source plus OAT. A missing mag check
on a flight that had a takeoff is a caution, not a failed drop.

Altitude is `press_alt` when that table exists, otherwise `gps.alt_msl`.
The scorecard names which one it used. IAS comes from `airspeed.ias_kt`.
A series that sits on 230 kt, or that does not agree with groundspeed, is
reported as unusable and the climb-speed check is skipped.

CHT3 is compared with the mean of the other cylinders in climb and cruise.
Eric's probe runs about 14–18°F hot, with a few degrees of wiggle. The
summary correlates that offset with inferred boost use once three or more
flights have an offset.

## Tuning

`~/kingfisher/engine/limits.json` is a flat object of override keys. Missing
file means the defaults. Unknown keys are ignored and printed. Example:

```json
{
  "cht_high_f": 400,
  "cold_oil_f": 160,
  "taxi_lean_ff_gph": 7
}
```

Pass `--limits path` on the CLI to point somewhere else. The web UI reads
the default path.

## Cache and duplicates

`analysis-cache/engine/<session>.json` plus `summary.json`. The web response
shifts event times with `clock_offsets` so a click lines up with the chart;
the cache itself keeps raw `ts_ns`.

`jpi-auto` records suspected duplicate sessions in
`~/kingfisher/engine/ingest.json` under `flags`. See `deploy/jpi/verify.md`.
