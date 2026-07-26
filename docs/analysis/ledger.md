# Flight data ledger

_Auto-generated 2026-07-26T00:58:04Z. Re-run `python scripts/analyze_flights.py catalog` (and `health` for flights). Hand notes live below the marker or in `*.md` sidecars._

## Summary

| Class | Count |
|-------|------:|
| `flight` | 10 |
| `taxi_only` | 6 |
| `soak` | 40 |
| `unknown` | 9 |
| **total** | **65** |

Classification: `flight` if GPS max groundspeed ≥ 40 kt (or sidecar override); `taxi_only` 5–40 kt; long stationary → `soak`; tiny/short → `no_info`. See [README](README.md).

## Flights

| File | Dur (h) | Max gs | Grade | Coverage | Airborne (min) | Pitot | Missing | Notes |
|------|--------:|-------:|-------|----------|---------------:|:-----:|---------|-------|
| `20260613T152117Z_n456t.db` | 6.79 | 108 | warn | late_start | 373.8 | Y | — | — |
| `20260621T185102Z_n456t.db` | 0.64 | 74 | ok | full | 11.5 | Y | — | This was a flight. Turned kingfisher on in the hangar Pulled |
| `20260701T152757Z_n456t.db` | 5.43 | 109 | warn | late_start | 321.1 | — | — | 5 pre-session pod rows (ignored for gaps |
| `20260711T133157Z_n456t.db` | 0.76 | 104 | warn | early_end | 31.0 | — | — | — |
| `20260711T165913Z_n456t.db` | 2.07 | 112 | warn | late_start | 32.6 | — | — | — |
| `20260711T193804Z_n456t.db` | 4.16 | 107 | warn | early_end | 241.6 | — | — | — |
| `20260717T173845Z_n456t.db` | 4.30 | 100 | warn | early_end | 211.6 | — | — | pod gaps×1; 1 aligned pod gaps >1s after session sta; 297 pre-session pod rows (ignored for ga |
| `20260718T165100Z_n456t.db` | 2.77 | 90 | warn | late_start | 148.2 | — | — | 606 pre-session pod rows (ignored for ga |
| `20260722T174914Z_n456t.db` | 2.56 | 105 | warn | late_start | 131.4 | — | — | pod gaps×1; 1 aligned pod gaps >1s after session sta; 485 pre-session pod rows (ignored for ga |
| `20260722T203620Z_n456t.db` | 3.68 | 109 | ok | full | 204.9 | — | — | 120 pre-session pod rows (ignored for ga |

## Soaks (stationary / overnight / hangar)

| File | MB | Dur (h) | Sensors | Preview |
|------|---:|--------:|---------|---------|
| `20260725T164134Z_n456t.db` | 282 | 8.28 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260725T151927Z_n456t.db` | 52 | 1.38 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260725T022149Z_n456t.db` | 306 | 8.64 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260717T125156Z_n456t.db` | 71 | 3.80 | ahrs, clock_offsets, geo, gps, icm45686_accel, icm | — |
| `20260717T121847Z_n456t.db` | 10 | 0.54 | ahrs, clock_offsets, geo, gps, icm45686_accel, icm | — |
| `20260716T223127Z_n456t.db` | 10 | 0.65 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260715T222229Z_n456t.db` | 163 | 4.44 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260715T214837Z_n456t.db` | 21 | 0.54 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260715T015734Z_n456t.db` | 734 | 19.60 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260715T000231Z_n456t.db` | 29 | 1.84 | ahrs, clock_offsets, gps, icm45686_accel, icm45686 | — |
| `20260714T215728Z_n456t.db` | 31 | 2.01 | ahrs, clock_offsets, gps, icm45686_accel, icm45686 | — |
| `20260714T184157Z_n456t.db` | 13 | 0.37 | ahrs, bmp581, bq27441, clock_offsets, compass, gps | — |
| `20260714T182530Z_n456t.db` | 9 | 0.27 | ahrs, bmp581, bq27441, clock_offsets, compass, gps | — |
| `20260714T100315Z_n456t.db` | 271 | 8.04 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260714T084125Z_n456t.db` | 25 | 1.36 | ahrs, clock_offsets, geo, gps, icm45686_accel, icm | — |
| `20260714T074757Z_n456t.db` | 9 | 0.28 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260714T000214Z_n456t.db` | 52 | 1.64 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260713T232257Z_n456t.db` | 21 | 0.66 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260713T100828Z_n456t.db` | 420 | 13.19 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260713T032522Z_n456t.db` | 16 | 0.50 | ahrs, bmp581, bq27441, compass, geo, gps, icm45686 | — |
| `20260713T020650Z_n456t.db` | 41 | 1.31 | ahrs, bmp581, bq27441, compass, geo, gps, icm45686 | — |
| `20260713T004315Z_n456t.db` | 44 | 1.39 | ahrs, bmp581, bq27441, compass, geo, gps, icm45686 | — |
| `20260712T234005Z_n456t.db` | 28 | 0.87 | ahrs, bmp581, bq27441, compass, geo, gps, icm45686 | — |
| `20260712T215501Z_n456t.db` | 52 | 1.65 | ahrs, bmp581, bq27441, compass, geo, gps, icm45686 | — |
| `20260712T212940Z_n456t.db` | 11 | 0.37 | ahrs, bmp581, bq27441, compass, gps, icm45686_acce | — |
| `20260625T114222Z_n456t.db` | 146 | 10.99 | bmp581, bq27441, compass, mmc5983, press_alt | — |
| `20260625T111519Z_n456t.db` | 5 | 0.41 | bmp581, bq27441, compass, mmc5983, press_alt | — |
| `20260621T092953Z_n456t.db` | 37 | 1.10 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260620T020819Z_n456t.db` | 54 | 2.17 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260619T175941Z_n456t.db` | 275 | 8.13 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260619T174335Z_n456t.db` | 9 | 0.27 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260619T160922Z_n456t.db` | 52 | 1.57 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260619T033607Z_n456t.db` | 426 | 12.55 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260618T161803Z_n456t.db` | 294 | 11.28 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260618T153304Z_n456t.db` | 19 | 0.57 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260618T135101Z_n456t.db` | 30 | 1.69 | ahrs, geo, gps, icm45686_accel, icm45686_gyro | — |
| `20260617T224337Z_n456t.db` | 271 | 15.12 | ahrs, geo, gps, icm45686_accel, icm45686_gyro | — |
| `20260617T213622Z_n456t.db` | 20 | 1.11 | ahrs, geo, gps, icm45686_accel, icm45686_gyro | — |
| `20260616T235138Z_n456t.db` | 6 | 0.33 | ahrs, geo, gps, icm45686_accel, icm45686_gyro | — |
| `20260609T015248Z_n456t.db` | 9 | 0.52 | ahrs, geo, gps, icm45686_accel, icm45686_gyro | — |

## Experiments

_None._

## Taxi-only

| File | MB | Dur (h) | Sensors | Preview |
|------|---:|--------:|---------|---------|
| `20260717T114959Z_n456t.db` | 1 | 0.13 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260714T191102Z_n456t.db` | 98 | 2.60 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260714T030640Z_n456t.db` | 18 | 0.56 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260621T103932Z_n456t.db` | 230 | 7.44 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260620T150659Z_n456t.db` | 437 | 13.25 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260617T001307Z_n456t.db` | 376 | 20.86 | ahrs, geo, gps, icm45686_accel, icm45686_gyro | — |

## No-information (archive candidates)

Tiny or empty sessions (desk restarts, failed boots). Safe to move to `~/kingfisher/flights-archive/` after review — do not delete forever yet.

_None._

## Future correlated sources

| Source | Status | Purpose |
|--------|--------|---------|
| Engine monitor dumps | planned (`~/kingfisher/engine/`) | RPM/MAP/EGT/CHT/FF sync for performance models |
| Weather (METAR/TAF, winds & temps aloft, turbulence) | planned | Atmosphere truth for TAS/wind, icing/turb context |
| Airband ATC audio | exists under `~/kingfisher/airband/` | Optional narrative correlation |
| IMU tempcal soaks | `~/kingfisher/imu_tempcal/` | Bias / TCO characterization |

<!-- HAND_NOTES_BEGIN -->
## Hand notes

- 2026-07-25: Initial catalog/health pass (100 sessions → 10 flights).
- Pattern flight `20260621T185102Z` is the only **ok/full** short session with pitot.
- Jul 17–22 flights show elevated aligned pod batch gaps (8–38); link health worth watching.
- Most Jul flights lack `ms4525`/`airspeed` tables — pitot not in schema for those sessions.
- Weather + engine dumps: planned under `~/kingfisher/weather/` and `~/kingfisher/engine/` (see README).
- 2026-07-25: Moved 35 `no_info` DBs (+ wal/shm) to `~/kingfisher/flights-archive/`.

<!-- HAND_NOTES_END -->
