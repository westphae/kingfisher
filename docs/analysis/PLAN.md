# Flight analysis & modeling plan

Roadmap from raw flight DBs → calibrated sensors → first-order derive →
flight-model inputs. Offline work lives under `analysis/`; flight DBs always
keep **raw** samples. Corrections are a layer (scripts first, optional online
later).

Related: [`README.md`](README.md) (tooling), [`../icm45686-bias-compensation.md`](../icm45686-bias-compensation.md)
(IMU online apply design), [`../timestamps.md`](../timestamps.md).

```mermaid
flowchart LR
  subgraph raw [Raw logs]
    IMU[icm45686]
    BARO[bmp581]
    MAG[mmc5983]
    PITOT[ms4525]
    GPS[gps]
  end
  subgraph cal [Calibration layer]
    Noise[Noise / ODR study]
    AccelCal[Accel scale+bias]
    GyroCal[Gyro bias+TCO+stability]
    MagCal[Hard/soft iron + mount]
    BaroCal[Baro static + lag]
  end
  subgraph der1 [First-order derive]
    AHRS[AHRS]
    HDG[Compass]
    ALT[Altitude]
    AS[Airspeed]
  end
  subgraph der2 [Flight model inputs later]
    WIND[Wind / TAS]
    PERF[Perf / weight / CG]
    AOA[AoA probe]
  end
  raw --> Noise --> AccelCal --> GyroCal --> MagCal --> BaroCal
  AccelCal --> AHRS
  GyroCal --> AHRS
  MagCal --> HDG
  BARO --> ALT
  PITOT --> AS
  AHRS --> der2
  HDG --> der2
  ALT --> der2
  AS --> der2
  GPS --> der2
```

---

## Phase 0 — Motion windows ✓

**Status:** implemented (`analysis/windows.py`, CLI `windows`).

Label every session in `~/kingfisher/flights/` (aircraft **and** desk soaks)
into 1 s epochs, then merge contiguous runs:

| Label | Meaning |
|-------|---------|
| `stationary` | Still — calibration pool (“level windows”) |
| `taxi` | GPS gs in taxi band (default 5–40 kt) |
| `flight` | GPS gs ≥ 40 kt |
| `transient` | Bump / pick-up, or motion without GPS taxi/flight |

Without reliable GPS → only `stationary` / `transient`. Pre-session pod
backlog (`ts_ns < _session.start_time`) excluded.

**Parquet** (partitioned by session for append-friendly re-runs):

```
~/kingfisher/analysis-cache/windows/
  manifest.json
  epochs/session_id=<stem>/part.parquet
  segments/session_id=<stem>/part.parquet
  segments_all.parquet
```

```bash
uv run --project analysis python scripts/analyze_flights.py windows
```

---

## Phase 1 — Sensor noise & parameter study

**Status:** 1a done; 1b cruise profiles confirmed for ICM-45686, BMP581, and
MMC5983 (desk soaks in [`sensor_noise.md`](sensor_noise.md)). ICM: chip 200 Hz /
LPF 12.5 / boxcar→25 Hz. BMP: 25 Hz, OSR×32, IIR 3. MMC: BW 100 / ODR 20 Hz
(at datasheet floor). MS4525 / confirmation flight still open.
Tooling: `analysis/noise.py`, CLI `noise`.

**Goal:** quantify noise vs rate/scale/filter; recommend bench + aircraft
settings.

### 1a. Noise floor (stationary windows)

On `stationary` segments (soaks + hangar), for `icm45686_accel`,
`icm45686_gyro`, `bmp581`, `mmc5983`:

- Per-axis / magnitude σ, σ of 1 s means
- Allan deviation (or autocorrelation) where segment length allows
- Noise vs configured `sampling_frequency` from `sensor_attrs` when it differs
  across sessions
- Note stored rate vs chip ODR (e.g. IMU often stored ~10 Hz while chip ODR
  is hundreds of Hz)

**Deliverable:** `docs/analysis/sensor_noise.md` + plots under
`~/kingfisher/analysis-cache/noise/`; CLI `noise`.

### 1b. Parameter experiments

| Sensor | Knobs | Metrics |
|--------|-------|---------|
| ICM45686 | ODR, full-scale, HW filter if exposed | noise density, flight saturation |
| BMP581 | ODR / OSR / IIR | σ(P), lag vs GPS alt in climb |
| MMC5983 | ODR / bandwidth | σ(‖B‖), heading jitter after cal |

**Offline (now):** compare noise across sessions that already used different
attrs; recommend config profiles (`taxi_cal`, `cruise_log`, `dynamics`).

**Bench / flight (follow-up):** deliberate rate/scale sweeps with kingfisher
recording; one confirmation flight after choosing cruise settings.

---

## Phase 2 — Accel calibration (~10 vs 9.80665 m/s²)

Magnitude bias is almost certainly **scale (+ small bias)**.

\[
\mathbf{a}_{\text{true}} = \mathbf{S}\,(\mathbf{a}_{\text{raw}} - \mathbf{b})
\]

1. **Stationary bench** — Cockpit **More → Calibrate** (`internal/calibrate`):
   **cabin accel** = six-face diagonal \(k,l\); **cabin gyro** = one still dwell
   (~30 s, any orientation) + \(T_\mathrm{ref}\)-baked OFFUSER; **pod mag** =
   six-face place-and-hold. See [calibrate.md](calibrate.md).
2. **From flights (interim):** stationary windows → fit scale so
   \(s\cdot\mathrm{median}(|a|) = g_0\); optional small bias if parked level.
3. Persist coeffs (`calibration.*` in config + `~/kingfisher/calibration/*.json`);
   apply offline (and later Step-1 compensator). **DB stays raw.**

**Deliverable:** `analysis/cal_accel.py`, cal JSON, before/after \|a\| plots
(`analyze_flights.py cal-accel`).

---

## Phase 3 — Gyro: temperature vs time stability

**Status: analysis + table shipped** — see [gyro_tco.md](gyro_tco.md).
`calibration.gyro_tco` (knees + \(\Delta b\) at \(T_\mathrm{ref}=35\) °C);
Accept bakes OFFUSER to \(T_\mathrm{ref}\); UI boldface peels \(\Delta b(T)\).

Layers (see also ICM bias doc):

| Layer | Question | Method | Result |
|-------|----------|--------|--------|
| TCO | \(b(T)\) | Lab soaks (`~/kingfisher/imu_tempcal/`) + desktop still | Piecewise linear, knots (−10, 10, 30) °C; mid-band X ~2–3× DS |
| Residual vs time | Wander after removing \(b_0+k\Delta T\)? | Long still at stable T | Resid ~0.002–0.005 °/s RMS; drift ~0.001 °/s/h |
| Turn-on / warm-up | First N minutes | Cold start vs steady | Mostly thermal track; optional gate later |
| In-run random walk | Angle growth | Integrate corrected ω on static windows | Secondary to TCO; formal Allan optional |

**Fork resolved:** residual after TCO is small → **temp model + occasional ground refine**. Weak Layer-2 AHRS bias can remain; do not substitute online bias for \(b(T)\). OFFUSER is constant-only — still need the table.

---

## Phase 4 — Mag / mount location (mmc5983)

Rank candidate pod mounts with the same taxi pattern when possible:

1. ‖B‖ vs WMM after hard-iron (sphere fit from taxi turns).
2. Ellipsoid / soft-iron residual.
3. Heading vs GPS track (taxi 2–40 kt) after declination.
4. Later: interference vs RPM / electrical load (engine dump).

**Deliverable:** `docs/analysis/mag_mount.md` scorecard → `pod_mount_r` / cal.

---

## Phase 5 — First-order derived values

After Phases 2–4 on at least one golden flight (+ pitot flight after zero).

### 5a. Fix existing derive

| Device | Likely issues | Plan |
|--------|---------------|------|
| AHRS | Raw IMU scale/bias/mount; weak gyro bias | Calibrated IMU; online bias; validate vs GPS |
| Compass | Distortion, align, pod↔cabin frame | Mount + hard/soft iron; WMM align |
| press_alt | Baro lag/bias vs GPS | Lag estimate; optional GPS blend |
| airspeed | Pitot zero historically bad; OAT | Zero routine; TAS vs GPS−wind |

### 5b. Near-term fused parameters

Body load factor, track/drift angle, climb proxies, steady wind (needs airspeed
+ heading), density altitude.

### 5c. Pod temperature probe

Defer hardware until pitot is zeroed and TAS vs GPS (calm wind) shows OAT as
the limiter; BMP581 temp is a starting point only.

---

## Phase 6 — Toward a flight model (later)

| Topic | Needs |
|-------|--------|
| Weight / fuel / CG over time | Empty weight + fuel log / FF |
| Engine monitor | Dumps under `~/kingfisher/engine/` |
| Weather | METAR/TAF, winds & temps aloft, turb → `~/kingfisher/weather/` |
| AoA / multi-hole | New probe after airspeed trusted |
| Perf / aero model | Clean AHRS + TAS + weight + engine + density |

---

## Suggested order of work

1. Phase 0 windows — **done**
2. Phase 1a/1b noise + parameter recommendations
3. Phase 2 accel scale/bias (+ cabin OFFUSER) — **largely done**; mag six-face deferred
4. Phase 3 gyro TCO — **done** ([gyro_tco.md](gyro_tco.md)); optional offline/AHRS feed next
5. Phase 4 mag mount (when pod can be moved)
6. Phase 5 offline AHRS/compass/airspeed validation → then live derive
7. Phase 6 engine / weather / AoA / weight as parallel inputs

## Success criteria (gates)

- Hangar \|a\| within ~0.5% of \(g_0\) after accel cal
- Gyro residual after TCO acceptable for AHRS horizon
- Mag mount with taxi heading RMS vs GPS under a few degrees
- Offline AHRS pitch/roll plausible in cruise; compass tracks GPS in straight taxi
- Airspeed ~0 on ground after zero; TAS consistent with GPS in calm conditions
  before buying another temp sensor
