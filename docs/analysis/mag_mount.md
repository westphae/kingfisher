# Magnetometer and IMU mount

_Generated 2026-10-14T03:56:51Z by `analyze_flights.py cal`. Raw flight databases are not modified._

## What was fit

Groundspeed in the databases is m/s. Taxi is 4–23 m/s (about 8–45 kt): the GPS track is the heading, because there is no wind correction angle and the airplane is still on the surface. The wing-pod MMC5983 then swings the Earth field through a circle. The fit is

```
m = A · [cos ψ, sin ψ, 1]
```

with ψ the magnetic heading (true track minus `geo.declination`). Horizontal hard iron, horizontal soft-iron scale, and a yaw of the pod are in that matrix. The vertical part of the hard iron is separated from the Earth-field dip only by assuming the World Magnetic Model vertical component and a vertical scale equal to the mean horizontal scale. The sphere residual in flight is the check on that assumption.

The cabin IMU is a separate rigid body. It is removed between flights. `R` maps an IMU vector into a local-level frame aligned with the ground track when a taxi turn was available. Roll and pitch come from gravity while the airplane is parked or taxiing straight. They include the deck angle of the airplane sitting on its gear. Yaw comes from the turn.

Live AHRS is fed the IMU axes as though they were the aircraft axes. Its bank is compared with `atan(v · dψ/dt / g)` in turning flight. The bias is the horizon zero of that install. The scatter after removing the bias is how well the horizon tracks.

## Recommendation

Across 17 sessions with a usable gravity reference, ground roll spans 25.6° and ground pitch spans 7.4°. Pitch includes the airplane's sit angle, which should repeat at the same ramp, so most of a multi-degree pitch span is the box going back in differently.

`20260701T152757Z_n456t.db` is the far one, at roll +24°. The live AHRS on that recording read about the same roll, so the box really was leaned. The other 16 sessions span 7.1° of roll.

The cabin box does not repeat. The live AHRS assumes the IMU axes are the aircraft axes, so the horizon zero moves by that span from flight to flight. Bolt the box to the airframe if you want the live horizon to mean the same thing every time you turn it on. Post-flight estimation fixes the recording. It does not change the horizon the AHRS showed in the cockpit that day. Yaw was observable on 7 of 16 sessions that had a taxi turn. On the others the two halves of the turn did not agree, so yaw was withheld. Roll and pitch only need the airplane sitting still, or taxiing straight, for about half a minute.

IMU yaw relative to the nose spans 95.0° across 7 sessions where a taxi turn made it observable. That is a rotation about the vertical between installs. The live AHRS does not apply it, so it reads the IMU pitch axis as the aircraft pitch.

Where the taxi swing covered enough headings, held-out magnetic heading error has median 9.2° (n=9). That is the compass you can get from this pod after a per-flight taxi calibration, on the ground, with no wind correction.

Hard iron across 9 acceptable fits spans [66.6, 61.6, 209.8] µT in the sensor frame. The vertical component changes sign across the set, which is what a rotated pod does even if the nearby iron did not move. Keep the calibration per flight.

In turning flight the live AHRS bank matches a coordinated turn to median 10.4° once a per-flight bias is removed (n=13). That bias is the horizon zero of the install, and it is not the same number every flight.

The live compass on straight taxi does not follow the track. Scatter around its own bias has median 68° across 19 sessions, and 0 of those stay within 15° of the track for half the time. The taxi calibration above is an offline correction. The cockpit compass does not use it.

45 parked or straight-taxi changes between 2.5 and 8 µT have the shape of a flap, gear, or trim motor (a blip that returns, or a step that stays). 83 changes are 8 µT or larger, which is too big for those motors: hangar steel, the pod being moved, or a bad sample. Nothing in the log names the circuit. Clicking an event in the browser zooms the raw mag, track, and AHRS yaw.

None of the 50 desktop sessions produced an ellipsoid that passed the residual gate. A spread of tens of µT there is a sensor that was picked up or a leftover spike, not a clean tumble.

A taxi swing is a circle, not a sphere. Vertical soft iron is not separable from a scale factor, and the fit assumes the vertical scale equals the mean horizontal scale. Tumble the pod once on the bench if you want a real 3-axis soft-iron matrix. The ellipsoid fit is wired for a desktop session whose mag vector actually moves.

Skipped as too large (recorder left running): `20260806T225805Z_n456t.db`, `20260825T215757Z_n456t.db`, `20260830T192617Z_n456t.db`, `20260907T222933Z_n456t.db`, `20260926T103918Z_n456t.db`.

## Flights and taxi sessions

| Session | Mag | Hdg RMS ° | Span ° | Hard iron µT | Scale | Events | Roll ° | Pitch ° | Yaw ° | AHRS bank scatter ° |
|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|
| `20260613T152117Z_n456t.db` | not enough taxi with a valid track | — | 0 | — | — | 0 | 0.1 | -0.5 | — | — |
| `20260617T001307Z_n456t.db` | no mmc5983 | — | — | — | — | 0 | -2.9 | 2.2 | — | — |
| `20260620T150659Z_n456t.db` | not enough taxi with a valid track | — | 10 | — | — | 0 | -0.5 | 4.8 | — | — |
| `20260621T103932Z_n456t.db` | not enough taxi with a valid track | — | 0 | — | — | 0 | -0.9 | 3.9 | — | — |
| `20260621T185102Z_n456t.db` | fair | 7.5 | 360 | [+13.7, +10.7, +83.2] | 1.01/0.99 | 6 | 4.0 | -0.5 | 175.0 | 5.8 |
| `20260701T152757Z_n456t.db` | weak | 32.1 | 170 | [+29.7, +24.3, +58.4] | 1.02/0.62 | 1 | 23.5 | -3.9 | 140.0 | — |
| `20260711T133157Z_n456t.db` | weak | 20.5 | 130 | [-17.0, +59.9, +97.1] | 1.08/0.78 | 2 | 4.1 | -4.5 | — | — |
| `20260711T165913Z_n456t.db` | fair | 10.9 | 150 | [-7.2, +40.6, +111.0] | 1.11/0.99 | 25 | 2.6 | -3.2 | — | 6.8 |
| `20260711T193804Z_n456t.db` | fair | 5.3 | 160 | [-5.3, +43.9, +108.5] | 1.03/0.93 | 6 | 2.7 | -2.5 | — | 12.0 |
| `20260714T030640Z_n456t.db` | not enough taxi with a valid track | — | 10 | — | — | 0 | 0.1 | -0.7 | — | — |
| `20260714T191102Z_n456t.db` | not enough taxi with a valid track | — | 0 | — | — | 0 | 0.7 | 1.1 | — | — |
| `20260717T114959Z_n456t.db` | no magnetometer | — | — | — | — | 0 | — | — | — | — |
| `20260717T173845Z_n456t.db` | fair | 9.2 | 240 | [-5.3, +55.8, +142.9] | 0.99/0.90 | 16 | -0.7 | -5.1 | — | — |
| `20260718T165100Z_n456t.db` | fair | 11.2 | 210 | [+1.4, +27.8, +138.8] | 1.30/1.00 | 5 | 2.0 | -1.0 | — | 10.5 |
| `20260722T174914Z_n456t.db` | weak | 15.6 | 180 | [-6.4, +55.6, +125.7] | 1.11/0.87 | 5 | -0.5 | -2.9 | — | 11.3 |
| `20260722T203620Z_n456t.db` | fair | 9.3 | 260 | [-19.5, +26.2, +119.0] | 1.20/0.99 | 7 | 0.1 | -3.4 | 177.0 | 9.5 |
| `20260726T132602Z_n456t.db` | fair | 7.5 | 320 | [+47.1, +50.7, +135.7] | 0.99/0.95 | 9 | -0.3 | -2.8 | — | 12.0 |
| `20260726T212723Z_n456t.db` | fair | 10.6 | 320 | [+26.3, +49.1, +136.5] | 1.04/0.92 | 6 | -2.1 | -2.2 | 178.0 | 11.4 |
| `20260731T222236Z_n456t.db` | not enough taxi with a valid track | — | 0 | — | — | 1 | -0.5 | 2.4 | — | — |
| `20260806T225805Z_n456t.db` | — | — | — | — | — | 0 | — | — | — | — |
| `20260825T215757Z_n456t.db` | — | — | — | — | — | 0 | — | — | — | — |
| `20260830T155106Z_n456t.db` | weak | 20.6 | 340 | [-47.3, +37.4, +115.9] | 1.08/0.82 | 1 | -1.3 | -2.8 | — | 9.2 |
| `20260830T192617Z_n456t.db` | — | — | — | — | — | 0 | — | — | — | — |
| `20260907T021724Z_n456t.db` | not enough taxi with a valid track | — | 0 | — | — | 6 | 1.4 | -1.4 | — | — |
| `20260907T143517Z_n456t.db` | fair | 7.2 | 290 | [-14.5, -5.8, -66.9] | 1.03/0.92 | 7 | -0.4 | -4.0 | — | 2.4 |
| `20260907T210324Z_n456t.db` | weak | 20.1 | 280 | [+8.3, -13.4, -69.4] | 1.19/0.96 | 0 | -1.3 | -6.0 | — | 5.7 |
| `20260907T222933Z_n456t.db` | — | — | — | — | — | 0 | — | — | — | — |
| `20260913T134019Z_n456t.db` | weak | 34.4 | 350 | [-12.9, +19.5, -15.3] | 1.15/0.77 | 16 | 5.0 | -0.6 | 83.0 | 15.0 |
| `20260913T203531Z_n456t.db` | weak | 12.8 | 240 | [-29.1, +8.9, -40.8] | 1.82/0.99 | 7 | 4.4 | -1.3 | 91.0 | 10.4 |
| `20260921T001442Z_n456t.db` | weak | 19.7 | 190 | [-28.1, +12.6, -13.0] | 1.12/0.94 | 7 | 4.7 | -0.8 | — | — |
| `20260921T013441Z_n456t.db` | weak | 36.0 | 260 | [-38.1, -29.0, -9.6] | 1.23/0.90 | 2 | -1.2 | -7.9 | 90.0 | — |
| `20260926T103918Z_n456t.db` | — | — | — | — | — | 0 | — | — | — | — |

## Desktop soaks

| Session | Wander µT | Spread µT | Ellipsoid center µT |
|---|---:|---:|---|
| `20260618T153304Z_n456t.db` | 42.72 | 227.9 | — |
| `20260618T161803Z_n456t.db` | 12.91 | 126.2 | — |
| `20260619T033607Z_n456t.db` | 12.49 | 260.4 | — |
| `20260619T160922Z_n456t.db` | 1.01 | 4.1 | — |
| `20260619T174335Z_n456t.db` | 0.60 | 1.4 | — |
| `20260619T175941Z_n456t.db` | 41.83 | 283.9 | — |
| `20260620T020819Z_n456t.db` | 0.08 | 0.4 | — |
| `20260621T092953Z_n456t.db` | 0.49 | 1.8 | — |
| `20260625T111519Z_n456t.db` | 22.41 | 60.0 | — |
| `20260625T114222Z_n456t.db` | 24.52 | 76.6 | — |
| `20260712T212940Z_n456t.db` | 0.08 | 0.5 | — |
| `20260712T215501Z_n456t.db` | 2.74 | 10.6 | — |
| `20260712T234005Z_n456t.db` | 0.07 | 0.4 | — |
| `20260713T004315Z_n456t.db` | 0.09 | 0.5 | — |
| `20260713T020650Z_n456t.db` | 0.12 | 1.9 | — |
| `20260713T032522Z_n456t.db` | 0.10 | 0.5 | — |
| `20260713T100828Z_n456t.db` | 0.18 | 1.0 | — |
| `20260713T232257Z_n456t.db` | 0.07 | 0.5 | — |
| `20260714T000214Z_n456t.db` | 0.17 | 0.7 | — |
| `20260714T074757Z_n456t.db` | 1.07 | 3.2 | — |
| `20260714T100315Z_n456t.db` | 0.62 | 3.2 | — |
| `20260714T182530Z_n456t.db` | 0.48 | 1.5 | — |
| `20260714T184157Z_n456t.db` | 0.48 | 1.7 | — |
| `20260715T015734Z_n456t.db` | 0.55 | 2.8 | — |
| `20260715T214837Z_n456t.db` | 0.72 | 4.7 | — |
| `20260715T222229Z_n456t.db` | 1.40 | 13.7 | — |
| `20260716T223127Z_n456t.db` | — | — | — |
| `20260725T022149Z_n456t.db` | 0.47 | 2.3 | — |
| `20260725T151927Z_n456t.db` | 0.10 | 0.6 | — |
| `20260725T164134Z_n456t.db` | 1.31 | 7.6 | — |
| `20260803T023253Z_n456t.db` | 2.24 | 63.2 | — |
| `20260803T110148Z_n456t.db` | 5.59 | 58.0 | — |
| `20260803T123238Z_n456t.db` | 18.11 | 137.0 | — |
| `20260803T132632Z_n456t.db` | 5.76 | 112.4 | — |
| `20260803T180254Z_n456t.db` | 0.09 | 0.6 | — |
| `20260803T222316Z_n456t.db` | 0.27 | 0.8 | — |
| `20260804T024244Z_n456t.db` | 0.88 | 4.7 | — |
| `20260804T164247Z_n456t.db` | 1.13 | 29.7 | — |
| `20260804T210102Z_n456t.db` | 0.09 | 0.6 | — |
| `20260804T215233Z_n456t.db` | 0.09 | 0.6 | — |
| `20260804T234939Z_n456t.db` | 0.09 | 0.5 | — |
| `20260805T002857Z_n456t.db` | 0.11 | 0.6 | — |
| `20260805T024431Z_n456t.db` | 0.20 | 0.9 | — |
| `20260805T160725Z_n456t.db` | 0.08 | 0.5 | — |
| `20260805T164904Z_n456t.db` | 0.08 | 0.6 | — |
| `20260805T175641Z_n456t.db` | 0.08 | 0.6 | — |
| `20260825T002929Z_n456t.db` | 35.12 | 198.2 | — |
| `20260825T021314Z_n456t.db` | 1.96 | 51.0 | — |
| `20260825T184050Z_n456t.db` | 0.11 | 0.7 | — |
| `20260825T212154Z_n456t.db` | 0.09 | 0.5 | — |

## Notes for the next pass

- `analysis/windows.py` compares raw `gps.gs` with 5 and 40 as if they were knots. The column is m/s, so those labels are about 10 kt and 78 kt. The calibration here does not use the window parquet. Fix the scale before trusting window labels for taxi.
- Naming a transient as flap or gear needs either a pilot mark in howgozit or a current sense on that circuit. The shape is already in the table.
- Engine RPM from the JPI dumps is not time-aligned yet. A steady mag offset that follows RPM would be alternator or ignition hard iron, distinct from the short motor events.
- One bench tumble of the pod, the same six-face ritual as the cabin accel, would replace the vertical-scale assumption with a measured soft-iron matrix.

