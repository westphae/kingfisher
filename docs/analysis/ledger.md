# Flight data ledger

_Auto-generated 2026-10-14T01:58:55Z. Re-run `python scripts/analyze_flights.py catalog` (and `health` for flights). Hand notes live below the marker or in `*.md` sidecars._

## Summary

| Class | Count |
|-------|------:|
| `flight` | 20 |
| `taxi_only` | 12 |
| `soak` | 69 |
| `no_info` | 22 |
| `unknown` | 21 |
| **total** | **144** |

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
| `20260717T173845Z_n456t.db` | 4.30 | 100 | warn | early_end | 211.6 | — | — | pod gaps×1; startup fallback: no GPS fix before DB o; 1 aligned pod gaps >1s after session sta; 297 pre-session pod rows (ignored for ga |
| `20260718T165100Z_n456t.db` | 2.77 | 90 | warn | late_start | 148.2 | — | — | startup fallback: Pi-vs-GPS offset excee; 606 pre-session pod rows (ignored for ga |
| `20260722T174914Z_n456t.db` | 2.56 | 105 | warn | late_start | 131.4 | — | — | pod gaps×1; startup fallback: no GPS fix before DB o; 1 aligned pod gaps >1s after session sta; 485 pre-session pod rows (ignored for ga |
| `20260722T203620Z_n456t.db` | 3.68 | 109 | ok | full | 204.9 | — | — | 120 pre-session pod rows (ignored for ga |
| `20260726T132602Z_n456t.db` | 0.65 | 101 | warn | early_end | 25.3 | — | — | 89 pre-session pod rows (ignored for gap |
| `20260726T212723Z_n456t.db` | 0.74 | 92 | warn | late_start | 10.7 | — | — | startup fallback: no GPS fix before DB o; 199 pre-session pod rows (ignored for ga |
| `20260830T155106Z_n456t.db` | 1172.28 | 104 | bad | early_end | 50.3 | — | — | Crosswind practice and T&G at 6R2, GAMI check at 9500 MSL. P; pod gaps×7; clock_offsets: wall−monotonic jumped +11; startup fallback: no GPS fix before DB o; RTC did not hold time at boot; icm45686_accel timestamps never followed; icm45686_gyro timestamps never followed ; 7 aligned pod gaps >1s after session sta; 163 pre-session pod rows (ignored for ga |
| `20260830T192617Z_n456t.db` | 174.74 | 43 | warn | partial | 0.0 | — | compass, bmp581, mmc5983, bq27441 | RTC did not hold time at boot; missing cabin: compass; missing pod: bmp581, mmc5983, bq27441 |
| `20260907T143517Z_n456t.db` | 1.00 | 91 | warn | early_end | 43.5 | Y | bmp581 | missing pod: bmp581; 110 pre-session pod rows (ignored for ga |
| `20260907T210324Z_n456t.db` | 6.55 | 96 | bad | early_end | 45.4 | Y | bmp581 | pod gaps×9; clock_offsets: wall−monotonic jumped +5.; startup fallback: no GPS fix before DB o; RTC did not hold time at boot; icm45686_accel timestamps never followed; icm45686_gyro timestamps never followed ; missing pod: bmp581; 9 aligned pod gaps >1s after session sta; 242 pre-session pod rows (ignored for ga |
| `20260913T134019Z_n456t.db` | 0.73 | 83 | warn | full | 14.2 | Y | bmp581 | clock_offsets: wall−monotonic jumped +0.; startup fallback: no GPS fix before DB o; missing pod: bmp581; 65 pre-session pod rows (ignored for gap |
| `20260913T203531Z_n456t.db` | 3.06 | 112 | warn | early_end | 169.7 | Y | bmp581 | pod gaps×1; missing pod: bmp581; 1 aligned pod gaps >1s after session sta; 18 pre-session pod rows (ignored for gap |
| `20260921T001442Z_n456t.db` | 1.03 | 103 | warn | early_end | 28.2 | Y | bmp581 | pod gaps×3; clock_offsets: wall−monotonic jumped +0.; startup fallback: no GPS fix before DB o; missing pod: bmp581; 3 aligned pod gaps >1s after session sta; 116 pre-session pod rows (ignored for ga |
| `20260921T013441Z_n456t.db` | 2.16 | 105 | warn | late_start | 122.8 | Y | bmp581 | missing pod: bmp581; 119 pre-session pod rows (ignored for ga |

## Soaks (stationary / overnight / hangar)

| File | MB | Dur (h) | Sensors | Preview |
|------|---:|--------:|---------|---------|
| `NOTIME_0002_n456t.db` | 10 | 116.56 | ahrs, clock_offsets, gps, icm45686_accel, icm45686 | — |
| `20260926T001526Z_n456t.db` | 12 | 0.75 | ahrs, clock_offsets, gps, icm45686_accel, icm45686 | — |
| `20260912T181530Z_n456t.db` | 5 | 0.27 | ahrs, clock_offsets, geo, gps, icm45686_accel, icm | — |
| `20260830T175754Z_n456t.db` | 28 | 1174.42 | ahrs, clock_offsets, geo, gps, icm45686_accel, icm | — |
| `20260825T212154Z_n456t.db` | 11 | 1056.72 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260825T184050Z_n456t.db` | 52 | 1.71 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260825T021314Z_n456t.db` | 12 | 0.44 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260825T002929Z_n456t.db` | 35 | 1.26 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260821T033826Z_n456t.db` | 2654 | 86.82 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260817T203218Z_n456t.db` | 2322 | 79.06 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260815T205503Z_n456t.db` | 1330 | 47.62 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260805T183625Z_n456t.db` | 853 | 28.30 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260805T175641Z_n456t.db` | 19 | 0.61 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260805T164904Z_n456t.db` | 30 | 0.99 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260805T160725Z_n456t.db` | 12 | 0.39 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260805T024431Z_n456t.db` | 403 | 13.38 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260805T002857Z_n456t.db` | 63 | 2.09 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260804T234939Z_n456t.db` | 20 | 0.66 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260804T215233Z_n456t.db` | 59 | 1.95 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260804T210102Z_n456t.db` | 26 | 0.86 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260804T164247Z_n456t.db` | 123 | 4.12 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260804T024244Z_n456t.db` | 404 | 14.00 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260803T222316Z_n456t.db` | 127 | 4.19 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260803T180254Z_n456t.db` | 86 | 2.83 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260803T132632Z_n456t.db` | 123 | 4.09 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | MMC5983 cruise confirmation after flash (BW 100 /  |
| `20260803T123238Z_n456t.db` | 27 | 0.90 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | Desk soak for MMC5983 cruise profile: BW **100 Hz* |
| `20260803T110148Z_n456t.db` | 53 | 1.51 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | Desk soak after flashing BMP581 cruise profile (OD |
| `20260803T023253Z_n456t.db` | 324 | 8.46 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260726T231944Z_n456t.db` | 67 | 3.69 | ahrs, clock_offsets, geo, gps, icm45686_accel, icm | — |
| `20260725T164134Z_n456t.db` | 715 | 19.64 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
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
| _… +29 more_ | | | | |

## Experiments

_None._

## Taxi-only

| File | MB | Dur (h) | Sensors | Preview |
|------|---:|--------:|---------|---------|
| `20260926T103918Z_n456t.db` | 8138 | 423.32 | ahrs, clock_offsets, geo, gps, icm45686_accel, icm | — |
| `20260907T222933Z_n456t.db` | 2152 | 112.10 | ahrs, clock_offsets, geo, gps, icm45686_accel, icm | — |
| `20260907T021724Z_n456t.db` | 287 | 11.28 | ahrs, airspeed, bmp581, bq27441, clock_offsets, co | — |
| `20260825T215757Z_n456t.db` | 2770 | 1152.55 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260806T225805Z_n456t.db` | 6286 | 213.95 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260731T222236Z_n456t.db` | 1007 | 52.05 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260717T114959Z_n456t.db` | 1 | 0.13 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260714T191102Z_n456t.db` | 98 | 2.60 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260714T030640Z_n456t.db` | 18 | 0.56 | ahrs, bmp581, bq27441, clock_offsets, compass, geo | — |
| `20260621T103932Z_n456t.db` | 230 | 7.44 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260620T150659Z_n456t.db` | 437 | 13.25 | ahrs, airspeed, bmp581, bq27441, compass, geo, gps | — |
| `20260617T001307Z_n456t.db` | 376 | 20.86 | ahrs, geo, gps, icm45686_accel, icm45686_gyro | — |

## No-information (archive candidates)

Tiny or empty sessions (desk restarts, failed boots). Safe to move to `~/kingfisher/flights-archive/` after review — do not delete forever yet.

**22 files**, ~14 MB total.

<details><summary>File list</summary>

- `20260803T110011Z_n456t.db` (0.11 MB)
- `20260803T110020Z_n456t.db` (0.93 MB)
- `20260803T173146Z_n456t.db` (0.67 MB)
- `20260803T175858Z_n456t.db` (2.07 MB)
- `20260805T024331Z_n456t.db` (0.31 MB)
- `20260805T024356Z_n456t.db` (0.39 MB)
- `20260805T160709Z_n456t.db` (0.20 MB)
- `20260805T160721Z_n456t.db` (0.10 MB)
- `20260805T163052Z_n456t.db` (0.14 MB)
- `20260805T164006Z_n456t.db` (1.09 MB)
- `20260805T183344Z_n456t.db` (1.25 MB)
- `20260805T183602Z_n456t.db` (0.29 MB)
- `20260806T225441Z_n456t.db` (1.76 MB)
- `20260821T033613Z_n456t.db` (1.18 MB)
- `20260825T015210Z_n456t.db` (1.84 MB)
- `20260825T183646Z_n456t.db` (0.86 MB)
- `20260912T140837Z_n456t.db` (0.17 MB)
- `20260921T013407Z_n456t.db` (0.09 MB)
- `20260926T012152Z_n456t.db` (0.10 MB)
- `20260926T095903Z_n456t.db` (0.00 MB)
- `20260926T103219Z_n456t.db` (0.11 MB)
- `NOTIME_0001_n456t.db` (0.13 MB)

</details>

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
