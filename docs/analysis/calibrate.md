# Stationary calibration

Bench procedures for cabin ICM-45686 (accel and gyro separately) and pod mag
(MMC5983). Guided UI: cockpit **More → Calibrate** (`#/calibrate`).

## Cabin accelerometer (6-face)

1. Place the case on a **table**, on each of six faces (`+Z…−Y`), any order.
2. **Do not** tip by hand — motion corrupts \(\lVert a\rVert = g_0\).
3. App detects the face from the dominant accel axis + sign.
4. Hold still → green countdown (~2.5 s) → ~8 s hub average → next face.
5. Accept programs **accel** `calibbias` (OFFUSER) and saves diagonal scale
   (software-only) into `calibration.cabin_imu`.

## Cabin gyro (still dwell)

Six-face gyro means showed no meaningful orientation dependence (face scatter
~0.002–0.015 °/s). Gyro cal is therefore a **single** still average:

1. Place the case on the table in **any** stable orientation.
2. Stillness is monitored for the **whole** ~30 s window (accel variance +
   ‖ω‖); the UI warns if you bump/tilt. Accept remains allowed (imperfect but
   better than nothing — e.g. in-flight).
3. ~**30 s** hub average of `icm45686-gyro` (+ die `temp_c`).
4. Accept bakes OFFUSER to \(T_\mathrm{ref}\):
   \(\mu_\mathrm{ref}=\bar\omega-\Delta b(T_\mathrm{cal})\) using
   `calibration.gyro_tco` — see [gyro_tco.md](gyro_tco.md).
5. UI boldface peels \(\Delta b(T)\) only after `gyro_offuser_applied`.

OFFUSER programming pauses both IIO buffers and writes `calibbias` without a
concurrent config-reload attr apply (that race corrupted registers to ~±1 rad/s).

Accel and gyro Accepts merge into the same `calibration.cabin_imu` object and
do not overwrite each other’s fields. Artifacts:
`~/kingfisher/calibration/cabin_accel_*.json` and `cabin_gyro_*.json`.

## Pod magnetometer (6-face)

Same place-and-hold ritual on the pod case; diagonal soft-iron + hard-iron.

## OFFUSER / soft display

| Flag | Soft constant bias | Soft other |
|------|--------------------|------------|
| `accel_offuser_applied` | skip accel \(l\) | scale \(k\) always soft |
| `gyro_offuser_applied` | skip gyro constant | \(\Delta b(T)\) always soft when table present |

Legacy `offuser_applied` alone is treated as both sensors programmed (migrated
on config load).

**Flight DB samples stay raw** (IIO after on-chip OFFUSER).

## Fits (magkal analogs)

| Target | Model |
|--------|--------|
| Accel | \(a_{\mathrm{corr},i}=k_i(a_{\mathrm{raw},i}-l_i)\), \(\lVert a_{\mathrm{corr}}\rVert\approx g_0\) |
| Gyro | Bias = mean \(\bar{\boldsymbol{\omega}}\) over one still dwell @ \(T_\mathrm{cal}\) |
| Mag | Diagonal soft-iron + hard-iron from opposite-face pairs |

**Full \(3\times3\,K\)** is not fit from six faces alone.

Quality:

- Warn if mean \(\lVert a_{\mathrm{corr}}\rVert\) is >0.5% from \(g_0\).
- Warn if face off-axis residual is large (case skew; still accepted).
- Mag: warn if \(\lVert B\rVert\) scatter after correction is large.

## Offline

```bash
uv run --project analysis python scripts/analyze_flights.py cal-accel \
  --json ~/kingfisher/calibration/cabin_accel_….json --plot
```

See also `analysis/cal_accel.py` and [PLAN.md](PLAN.md) Phase 2.
