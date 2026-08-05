# Phase 3 — Gyro temperature vs time

Status: **analysis done; table shipped** (2026-08-05). Soft UI applies \(\Delta b(T)\); six-face Accept bakes OFFUSER to \(T_\mathrm{ref}\).

Data:

- Lab soaks (2026-07-14/15): `~/kingfisher/imu_tempcal/{run_20260714_coldsoak,run_20260714_freezersoak,run_20260715_hotbox}/` + plots in `plots/`
- Desktop still runs: flight DBs under `~/kingfisher/flights/` (summaries in `~/kingfisher/analysis-cache/gyro_tco/`)
- Fit artifact: `~/kingfisher/analysis-cache/gyro_tco/gyro_tco_fit.json`
- Code: `internal/config/gyro_tco.go` (`calibration.gyro_tco`), Accept bake-in in `internal/sensors/offuser.go`, UI in `static/display.js`

---

## Runtime model

Shape only (independent of cal epoch):

\[
\Delta b(T) = b(T) - b(T_\mathrm{ref}),\quad \Delta b(T_\mathrm{ref}) = 0
\]

| Stage | Action |
|-------|--------|
| Six-face | Measure \(\bar\omega\) at \(T_\mathrm{cal}\); store `temp_cal_c` |
| Accept | \(\mu_\mathrm{ref} = \bar\omega - \Delta b(T_\mathrm{cal})\) → OFFUSER + attrs; chip correct at \(T_\mathrm{ref}\) |
| UI boldface | After OFFUSER: \(\omega_\mathrm{cal} = \omega_\mathrm{raw} - \Delta b(T)\). Before Accept: also soft-subtract constant bias with shape to \(T\) |
| DB | Raw (IIO after on-chip OFFUSER) |

`calibration.gyro_tco.t_ref_c` defaults to **35 °C** (between desktop ~34.5 and summer flight ~40.5; changeable without reshaping knees).

Config keys use **knees** (break points in die °C), not “knots”.

---

## Shipped table (`DefaultGyroTCO`)

Re-fit from all three soak CSVs (974 still windows after relative motion gate): continuous piecewise-linear hats at knees \(-22,-10,10,30,55\) °C with a **per-run offset** nuisance, then zeroed at \(T_\mathrm{ref}=35\). Fit residual RMS ≈ 0.019 / 0.012 / 0.010 °/s (x/y/z).

| \(T\) (°C) | \(\Delta b_x\) | \(\Delta b_y\) | \(\Delta b_z\) (°/s) |
|------------|----------------|----------------|----------------------|
| −22 | +0.289 | −0.244 | +0.102 |
| −10 | +0.255 | −0.175 | +0.078 |
| +10 | +0.293 | +0.125 | +0.015 |
| +30 | +0.011 | +0.004 | −0.019 |
| **35** | **0** | **0** | **0** |
| +55 | −0.046 | −0.016 | +0.075 |

Linear interpolation between knees; clamp outside \([-22,55]\).

---

## Die temperature context (anchor choice)

| Cohort | Session median p10 / **p50** / p90 | Envelope |
|--------|-------------------------------------|----------|
| Flight (n=12, summer-heavy) | 34.9 / **40.5** / 46.0 °C | 27…51 |
| Desktop (n=67) | 28.3 / **34.5** / 41.0 °C | 24.5…47 |

\(T_\mathrm{ref}=35\) is a hangar-friendly default; raise toward 40 later if winter/flight data warrant.

---

## Analysis conclusions (unchanged)

- Mid-band TCO often 2–3× datasheet; Y non-monotonic → piecewise required.
- Cooling↔warming lag loop ~0.04 °/s on X; endpoints repeat ~0.01 °/s.
- After local linear \(b(T)\) at stable room T: resid ~0.002–0.005 °/s RMS — **temp model + occasional ground refine**, not heavy online bias.
- Accel: only Z TCO trustworthy; not in this table yet.

---

## Next

- Optional: offline apply in flight tools; Step-1 compensator / AHRS feed.
- Optional: accel-z TCO; Allan at fixed T.
- Re-Accept cabin IMU once after upgrade so OFFUSER is \(T_\mathrm{ref}\)-baked (existing OFFUSER was nulled at \(T_\mathrm{cal}\)).
