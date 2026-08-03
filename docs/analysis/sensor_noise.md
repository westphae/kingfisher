# Sensor noise & parameter study (Phase 1)

_Generated 2026-08-03T11:14:19Z from stationary windows (65/69 sessions). See [PLAN.md](PLAN.md)._

## Method (1a)

For each session with ≥120 s of `stationary` segments (from Phase 0 windows), load up to ~80k samples of accel/gyro/baro/mag from the longest still intervals. Report sample σ, σ of 1-second means, white-noise density approx `σ·√Δt`, observed median rate, and configured `sampling_frequency` from `sensor_attrs`.

## Aggregate results

| Channel | Sessions | Obs Hz | Config Hz | σ (med) | σ 1s (med) | σ/σ₁ₛ | Density `σ√Δt` | Unit |
|---------|----------|--------|-----------|---------|------------|-------|----------------|------|
| `bmp581.static_pressure_pa` | 48 | 50.00 | 50 | 13.31 | 13.29 | 1.00 | 2.087 | Pa |
| `icm45686_accel.accel_x` | 65 | 24.91 | 800 | 0.02674 | 0.009528 | 2.81 | 0.007208 | m/s² |
| `icm45686_accel.accel_y` | 65 | 24.91 | 800 | 0.02518 | 0.01015 | 2.48 | 0.007612 | m/s² |
| `icm45686_accel.accel_z` | 65 | 24.91 | 800 | 0.02168 | 0.007319 | 2.96 | 0.005722 | m/s² |
| `icm45686_gyro.anglvel_x` | 65 | 24.91 | 800 | 0.0003672 | 0.0001565 | 2.35 | 0.0001261 | rad/s |
| `icm45686_gyro.anglvel_y` | 65 | 24.91 | 800 | 0.0007167 | 0.0002165 | 3.31 | 0.0001922 | rad/s |
| `icm45686_gyro.anglvel_z` | 65 | 24.91 | 800 | 0.0002643 | 0.0001516 | 1.74 | 9.865e-05 | rad/s |
| `mmc5983.mag_x_ut` | 48 | 10.00 | 50 | 0.2732 | 0.1428 | 1.91 | 0.1203 | µT |
| `mmc5983.mag_y_ut` | 48 | 10.00 | 50 | 0.2732 | 0.1761 | 1.55 | 0.121 | µT |
| `mmc5983.mag_z_ut` | 48 | 10.00 | 50 | 0.2995 | 0.1567 | 1.91 | 0.08376 | µT |

### Accel scale hint (feeds Phase 2)

Median stationary ‖a‖ ≈ **9.9633** m/s² vs (g₀=9.80665). Crude multiplicative scale (g₀/‖a‖) ≈ **0.9843** (~-1.6%). Confirm with 6-position cal before applying online.

## Plots

Written under `~/kingfisher/analysis-cache/noise/` — one scale per sensor:

- **Accel (icm45686):** `std_by_channel_accel.png`, `std_vs_1s_accel.png`
- **Gyro (icm45686):** `std_by_channel_gyro.png`, `std_vs_1s_gyro.png`
- **Baro (bmp581):** `std_by_channel_bmp581.png`, `std_vs_1s_bmp581.png`
- **Mag (mmc5983):** `std_by_channel_mmc5983.png`, `std_vs_1s_mmc5983.png`
- **Accel ‖a‖ hist:** `accel_norm_hist.png`

## Findings (1a)

- **ICM45686:** `sensor_attrs` reports ~800 Hz ODR but stored/observed rate in DB is ~24.9 Hz. Noise figures below are for the **stored** stream (what AHRS sees unless you change publish/store rate).
- **BMP581:** stationary σ(P) ≈ 13.31 Pa (~1.1 cm barometric at SL); 1 s means σ ≈ 13.29 Pa.
- **MMC5983:** axis σ ≈ 0.273 µT at ~10 Hz stored.
- **Gyro:** axis σ ≈ 0.00037 rad/s (76 °/h rms) at ~25 Hz stored.
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

- For **calibration (Phase 2–3)**, prefer long `stationary` segments; 1 s averaging already cuts high-frequency noise substantially when `σ/σ₁ₛ` ≫ 1.
- For **live AHRS**, either keep ~10 Hz store and accept its noise, or raise publish rate only if the filter can use the extra bandwidth; raising chip ODR without raising store rate mainly helps on-chip filtering.
- Apply **accel scale** (Phase 2) before interpreting ‖a‖−g as noise.

## Next

Phase 2 — accel scale/bias from stationary windows + optional 6-position bench ([PLAN.md](PLAN.md)).

<!-- HAND_NOTES_BEGIN -->
## Confirmation soaks (cruise profiles)

### ICM-45686 — `20260803T023253Z_n456t`

Chip ODR **200 Hz**, UI LPF **12.5 Hz**, LN, accel ±4 g / gyro ±62.5 dps; boxcar → publish **25 Hz**.

| Channel | σ new | σ archive (med) | σ_new/σ_base |
|---------|------:|----------------:|-------------:|
| accel_x | 0.00299 | 0.0267 | **0.11** |
| accel_y | 0.00224 | 0.0264 | **0.08** |
| accel_z | 0.0027 | 0.0217 | **~0.12** |

Gyro ~1.5× quieter than archive median.

### BMP581 — `20260803T110148Z_n456t`

After pod firmware flash: ODR **25 Hz**, press OSR **×32**, temp OSR **×2**, IIR coeff **3**.
First **90 s** excluded (blew on sensor + relocated shelf; ~+9 Pa step).
~331 s clean stationary used for the auto noise pass.

| Metric | New profile | Archive median (OSR×1 @ ~50 Hz) |
|--------|------------:|--------------------------------:|
| Full-window σ(P) | **1.06 Pa** | 13.31 Pa (~**0.08×**) |
| Median 30 s chunk σ | **~0.17 Pa** | — |
| Observed rate | 25 Hz | 50 Hz |

Full-window σ is inflated by slow room/weather drift (~1 Pa/min in this soak).
Short-window / high-frequency noise is ~**80×** quieter than the old OSR×1 stream.
Allan σ(τ=1 s) on this soak ≈ **0.037 Pa**.

Applied via `pod.attrs` + `SetAttr` (`oversampling_*`, `iir_*`).
<!-- HAND_NOTES_END -->
