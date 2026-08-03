# Sensor noise & parameter study (Phase 1)

_Generated 2026-08-02T23:23:16Z from stationary windows (63/67 sessions). See [PLAN.md](PLAN.md)._

## Method (1a)

For each session with ≥120 s of `stationary` segments (from Phase 0 windows), load up to ~80k samples of accel/gyro/baro/mag from the longest still intervals. Report sample σ, σ of 1-second means, white-noise density approx `σ·√Δt`, observed median rate, and configured `sampling_frequency` from `sensor_attrs`.

## Aggregate results

| Channel | Sessions | Obs Hz | Config Hz | σ (med) | σ 1s (med) | σ/σ₁ₛ | Density `σ√Δt` | Unit |
|---------|----------|--------|-----------|---------|------------|-------|----------------|------|
| `bmp581.static_pressure_pa` | 46 | 50.00 | 50 | 13.44 | 13.65 | 0.98 | 2.146 | Pa |
| `icm45686_accel.accel_x` | 63 | 24.91 | 800 | 0.02674 | 0.01003 | 2.67 | 0.00725 | m/s² |
| `icm45686_accel.accel_y` | 63 | 24.91 | 800 | 0.02644 | 0.01117 | 2.37 | 0.007866 | m/s² |
| `icm45686_accel.accel_z` | 63 | 24.91 | 800 | 0.02177 | 0.007481 | 2.91 | 0.006167 | m/s² |
| `icm45686_gyro.anglvel_x` | 63 | 24.91 | 800 | 0.0003723 | 0.0001742 | 2.14 | 0.000129 | rad/s |
| `icm45686_gyro.anglvel_y` | 63 | 24.91 | 800 | 0.0007405 | 0.0002216 | 3.34 | 0.0001939 | rad/s |
| `icm45686_gyro.anglvel_z` | 63 | 24.91 | 800 | 0.0002696 | 0.0001538 | 1.75 | 0.0001047 | rad/s |
| `mmc5983.mag_x_ut` | 46 | 10.00 | 50 | 0.2983 | 0.1532 | 1.95 | 0.1294 | µT |
| `mmc5983.mag_y_ut` | 46 | 10.00 | 50 | 0.3098 | 0.1969 | 1.57 | 0.1255 | µT |
| `mmc5983.mag_z_ut` | 46 | 10.00 | 50 | 0.3001 | 0.181 | 1.66 | 0.09515 | µT |

### Accel scale hint (feeds Phase 2)

Median stationary ‖a‖ ≈ **9.9646** m/s² vs (g₀=9.80665). Crude multiplicative scale (g₀/‖a‖) ≈ **0.9841** (~-1.6%). Confirm with 6-position cal before applying online.

## Plots

Written under `~/kingfisher/analysis-cache/noise/` — one scale per sensor:

- **Accel (icm45686):** `std_by_channel_accel.png`, `std_vs_1s_accel.png`
- **Gyro (icm45686):** `std_by_channel_gyro.png`, `std_vs_1s_gyro.png`
- **Baro (bmp581):** `std_by_channel_bmp581.png`, `std_vs_1s_bmp581.png`
- **Mag (mmc5983):** `std_by_channel_mmc5983.png`, `std_vs_1s_mmc5983.png`
- **Accel ‖a‖ hist:** `accel_norm_hist.png`

## Findings (1a)

- **ICM45686:** `sensor_attrs` reports ~800 Hz ODR but stored/observed rate in DB is ~24.9 Hz. Noise figures below are for the **stored** stream (what AHRS sees unless you change publish/store rate).
- **BMP581:** stationary σ(P) ≈ 13.44 Pa (~1.1 cm barometric at SL); 1 s means σ ≈ 13.65 Pa.
- **MMC5983:** axis σ ≈ 0.298 µT at ~10 Hz stored.
- **Gyro:** axis σ ≈ 0.00037 rad/s (77 °/h rms) at ~25 Hz stored.
- Historical sessions share the **same** configured ODRs (accel/gyro 800, BMP/MMC 50) — little natural A/B for rate knobs in the archive; use σ(1 s means) as a proxy for heavier averaging / lower effective rate.

## Parameter recommendations (1b)

### Config profiles (proposed)

| Profile | Intent | ICM ODR (chip) | Store/publish | BMP | MMC | Notes |
|---------|--------|----------------|---------------|-----|-----|-------|
| `taxi_cal` | Mag taxi + ground cal | 100–200 Hz | 50–100 Hz | 20–50 Hz | 50–100 Hz | Lower full-scale if no saturation; prioritize mag bandwidth |
| `cruise_log` | Long flights, storage | 200–400 Hz | **10–20 Hz** (current~10) | 25–50 Hz | 10–25 Hz | Keep chip ODR above store rate for HW filter; reduce σ via averaging |
| `dynamics` | Maneuver / AHRS stress | 400–800 Hz | 50–100 Hz | 50 Hz | 50 Hz | Watch accel FS (±4 g today → rails at ~39 m/s²); consider ±8/16 g for aerobatics |

### Bench / flight sweeps still needed

Archive attrs do **not** vary. To finish 1b experimentally:

1. **ICM45686:** On the bench (stationary), record 5–10 min at chip ODR 100 / 200 / 400 / 800 Hz with the same store path; compare σ and σ(1 s). Optionally change accel FS ±2/±4/±8 g and confirm noise vs range.
2. **BMP581:** Sweep OSR/ODR if exposed; measure σ(P) and step response to a small height change (stairs) vs GPS/phone baro.
3. **MMC5983:** Sweep 10 / 50 / 100 Hz; measure σ(‖B‖) and heading jitter after a quick hard-iron fit while rotating the pod.
4. One **confirmation flight** on `cruise_log` after choosing settings; re-run `windows` + `noise` and compare to this baseline.

### Practical takeaway for now

- **ICM-45686 cruise profile (applied):** chip ODR **200 Hz**, UI LPF **12.5 Hz** (ODR/16), LN, accel **±4 g**, gyro **±62.5 dps**; kingfisher boxcars 8:1 to **`sample_hz` 25**. Requires `icm45686-mod` with UI LPF sysfs.
- For **calibration (Phase 2–3)**, prefer long `stationary` segments; 1 s averaging already cuts high-frequency noise substantially when `σ/σ₁ₛ` ≫ 1.
- Apply **accel scale** (Phase 2) before interpreting ‖a‖−g as noise.

## Next

Phase 2 — accel scale/bias from stationary windows + optional 6-position bench ([PLAN.md](PLAN.md)).
