# Sensor noise & parameter study (Phase 1)

_Generated 2026-08-03T13:37:16Z from stationary windows (67/71 sessions). See [PLAN.md](PLAN.md)._

## Method (1a)

For each session with ≥120 s of `stationary` segments (from Phase 0 windows), load up to ~80k samples of accel/gyro/baro/mag from the longest still intervals. Report sample σ, σ of 1-second means, white-noise density approx `σ·√Δt`, observed median rate, and configured `sampling_frequency` from `sensor_attrs`.

## Aggregate results

| Channel | Sessions | Obs Hz | Config Hz | σ (med) | σ 1s (med) | σ/σ₁ₛ | Density `σ√Δt` | Unit |
|---------|----------|--------|-----------|---------|------------|-------|----------------|------|
| `bmp581.static_pressure_pa` | 50 | 50.00 | 50 | 13.24 | 13.18 | 1.00 | 2 | Pa |
| `icm45686_accel.accel_x` | 67 | 24.91 | 800 | 0.02664 | 0.009218 | 2.89 | 0.007197 | m/s² |
| `icm45686_accel.accel_y` | 67 | 24.91 | 800 | 0.02498 | 0.009639 | 2.59 | 0.007095 | m/s² |
| `icm45686_accel.accel_z` | 67 | 24.91 | 800 | 0.02161 | 0.007287 | 2.97 | 0.005694 | m/s² |
| `icm45686_gyro.anglvel_x` | 67 | 24.91 | 800 | 0.0003609 | 0.000147 | 2.45 | 0.0001198 | rad/s |
| `icm45686_gyro.anglvel_y` | 67 | 24.91 | 800 | 0.0006597 | 0.0002125 | 3.10 | 0.0001894 | rad/s |
| `icm45686_gyro.anglvel_z` | 67 | 24.91 | 800 | 0.0002557 | 0.0001499 | 1.71 | 9.861e-05 | rad/s |
| `mmc5983.mag_x_ut` | 50 | 10.00 | 50 | 0.2678 | 0.1158 | 2.31 | 0.1013 | µT |
| `mmc5983.mag_y_ut` | 50 | 10.00 | 50 | 0.2697 | 0.1662 | 1.62 | 0.09608 | µT |
| `mmc5983.mag_z_ut` | 50 | 10.00 | 50 | 0.2984 | 0.1469 | 2.03 | 0.07156 | µT |

### Accel scale hint (feeds Phase 2)

Median stationary ‖a‖ ≈ **9.9625** m/s² vs (g₀=9.80665). Crude multiplicative scale (g₀/‖a‖) ≈ **0.9844** (~-1.6%). Confirm with 6-position cal before applying online.

## Plots

Written under `~/kingfisher/analysis-cache/noise/` — one scale per sensor:

- **Accel (icm45686):** `std_by_channel_accel.png`, `std_vs_1s_accel.png`
- **Gyro (icm45686):** `std_by_channel_gyro.png`, `std_vs_1s_gyro.png`
- **Baro (bmp581):** `std_by_channel_bmp581.png`, `std_vs_1s_bmp581.png`
- **Mag (mmc5983):** `std_by_channel_mmc5983.png`, `std_vs_1s_mmc5983.png`
- **Accel ‖a‖ hist:** `accel_norm_hist.png`

## Findings (1a)

- **ICM45686:** `sensor_attrs` reports ~800 Hz ODR but stored/observed rate in DB is ~24.9 Hz. Noise figures below are for the **stored** stream (what AHRS sees unless you change publish/store rate).
- **BMP581:** stationary σ(P) ≈ 13.24 Pa (~1.1 cm barometric at SL); 1 s means σ ≈ 13.18 Pa.
- **MMC5983:** axis σ ≈ 0.268 µT at ~10 Hz stored.
- **Gyro:** axis σ ≈ 0.00036 rad/s (74 °/h rms) at ~25 Hz stored.
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

### MMC5983 — `20260803T132632Z_n456t` (quiet location)

After firmware flash with `SetAttr` bandwidth knob. Cruise: BW **100 Hz**, ODR **20 Hz**.
Relocated mid-session (‖B‖ 89→62 µT); first **50 s** excluded. ~322 s clean.

| Metric | Quiet location | Prior desk soak @ 20 Hz | Archive median (~10 Hz) |
|--------|---------------:|------------------------:|------------------------:|
| σ(mag_x) | **0.038** µT | 0.043 µT | 0.273 µT |
| σ(mag_y) | **0.044** µT | 0.064 µT | 0.273 µT |
| σ(mag_z) | **0.063** µT | 0.065 µT | 0.300 µT |
| Observed rate | ~15 Hz | ~13 Hz | 10 Hz |

Axis σ ≈ datasheet **0.4 mG (0.04 µT)** floor. Archive median was the old ~10 Hz
harvest path + ambient interference, not BW. Raising BW above 100 Hz increases
noise; use **50 Hz / BW=100** for taxi cal.

`pod.attrs`: `in_mmc5983_sampling_frequency=20`, `in_mmc5983_bandwidth=100`.
<!-- HAND_NOTES_END -->
