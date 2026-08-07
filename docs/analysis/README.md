# Flight data analysis

Tools to catalog kingfisher SQLite sessions, grade sampling health, and keep an
ongoing ledger for modeling (compass, AHRS, performance) and cleanup.

## Quick start

Uses **uv** (workspace member `analysis/`; see [`docs/python.md`](../python.md)).
From repo root (Pi or workstation with a copy of `flights/`):

```bash
uv sync --all-packages   # or: uv sync --project analysis
uv run --project analysis python scripts/analyze_flights.py all
```

Outputs:

| Artifact | Default path |
|----------|----------------|
| Catalog JSON | `~/kingfisher/analysis-cache/catalog.json` |
| Health JSON | `~/kingfisher/analysis-cache/health.json` |
| Motion windows | `~/kingfisher/analysis-cache/windows/` (parquet) |
| Noise study | `~/kingfisher/analysis-cache/noise/` + `docs/analysis/sensor_noise.md` |
| Full plan | [`PLAN.md`](PLAN.md) |
| Ledger (git) | `docs/analysis/ledger.md` |

Commands:

```bash
uv run --project analysis python scripts/analyze_flights.py catalog
uv run --project analysis python scripts/analyze_flights.py health --flights-only
uv run --project analysis python scripts/analyze_flights.py windows
uv run --project analysis python scripts/analyze_flights.py noise
uv run --project analysis python scripts/analyze_flights.py report
uv run --project analysis python scripts/analyze_flights.py cal-accel --json ~/kingfisher/calibration/cabin_accel_….json --plot
uv run --project analysis python scripts/analyze_flights.py windows --file 20260722T203620Z_n456t.db

# Bench cal UI (cabin accel six-face + cabin gyro still + pod mag TBD): [calibrate.md](calibrate.md)
# Roadmap status: [PLAN.md](PLAN.md)

# Legacy sampling plots (matplotlib):
uv run --project analysis python scripts/analyze_flight_sampling.py --help
```

Dependencies (locked in root `uv.lock`): Python ≥3.11, `numpy`, `matplotlib`, `pyarrow`, `pandas`.

## Layout

```
analysis/                 # Python package (importable from scripts/)
scripts/analyze_flights.py
docs/analysis/
  README.md               # this file
  ledger.md               # ongoing catalog (regen preserves hand notes)
~/kingfisher/
  flights/                # *.db (+ optional *.md sidecars)
  flights-archive/        # move no_info here after review
  analysis-cache/         # regenerable JSON
  imu_tempcal/            # bias/TCO experiments
  engine/                 # planned: engine-monitor dumps
  weather/                # planned: METAR/TAF/winds aloft/turb
```

Recording stays on the Pi. Heavy modeling can run on a workstation against an
`rsync`/`scp` of `flights/` (and later `engine/`, `weather/`).

## Classification

| Class | Rule (v1) |
|-------|-----------|
| `flight` | GPS max groundspeed ≥ 40 kt, or sidecar `class: flight` |
| `taxi_only` | 5 ≤ max gs &lt; 40 kt |
| `soak` | Longer stationary session (desk/hangar/overnight) |
| `experiment` | Sidecar `class: experiment` or tags |
| `no_info` | Tiny (&lt;2 MB) or short (&lt;5 min) — archive candidate |

Override with a sidecar next to the DB, e.g. `20260621T185102Z_n456t.md`:

```yaml
---
class: flight
flightaware: https://...
engine_dump: ../engine/raw/foo.csv
tags: pattern, kcxo
---

Free-form notes…
```

## Motion windows (Phase 0)

Label every session in `flights/` (aircraft *and* desk soaks) into 1 s epochs,
then merge contiguous runs into segments:

| Label | Meaning |
|-------|---------|
| `stationary` | Still — use for calibration ("level windows") |
| `taxi` | GPS gs in taxi band (default 5–40 kt) |
| `flight` | GPS gs ≥ 40 kt |
| `transient` | Bump / pick-up, or motion without GPS taxi/flight (desk handling) |

Without reliable GPS, only `stationary` / `transient` are used (so desktop
sessions never become fake taxi/flight).

### Parquet layout (append-friendly)

One giant file would work for epochs (~1 Hz × hours × tens of sessions is only
tens–hundreds of MB), but **rewriting it on every run is painful**. Instead:

```
~/kingfisher/analysis-cache/windows/
  manifest.json
  epochs/session_id=<stem>/part.parquet    # 1 Hz labels + features
  segments/session_id=<stem>/part.parquet  # contiguous runs (small)
  segments_all.parquet                     # compacted segments (optional)
```

Re-running one session overwrites only that `session_id=` partition. Read with
pandas/pyarrow datasets:

```python
import pyarrow.dataset as ds
epochs = ds.dataset(".../windows/epochs", format="parquet", partitioning="hive")
segments = ds.dataset(".../windows/segments", format="parquet", partitioning="hive")
# or: pd.read_parquet(".../windows/segments_all.parquet")
```

## Health grades

For `class=flight` sessions:

- Per-table median Δt / mean Hz / gaps (threshold ≈ max(3×nominal, 250 ms))
- Aligned multi-sensor pod holes &gt;1 s → batch/link loss
- Coverage: time before first taxi, airborne minutes (gs≥40), after last motion
- Missing core cabin (`gps`, IMU, `ahrs`, …) or pod (`bmp581`, `mmc5983`, `bq27441`)

**Pre-session pod backlog is ignored for gap scoring.** Normal workflow is to
power the wing pod first, then start the cabin hub: aged UDP readings can land
with `ts_ns` before `_session.start_time`. Rates/gaps use only
`ts_ns >= start_time`. Pre-session row counts are noted but do not lower the
grade.

`icm45686_*` chip ODR in `sensor_attrs` (e.g. 800 Hz) vs ~10 Hz stored rows is
expected, not a dropout.

## Ledger hand notes

`docs/analysis/ledger.md` is regenerated by `report`. Content between
`<!-- HAND_NOTES_BEGIN -->` and `<!-- HAND_NOTES_END -->` is preserved.

## Modeling path (later)

| Goal | Primary inputs | Correlated later |
|------|----------------|------------------|
| Mag → compass | `mmc5983`, `geo`, taxi GPS | — |
| IMU → AHRS | `icm45686_*`, GPS, baro, temp | see `docs/icm45686-bias-compensation.md` |
| Pitot / airspeed | `ms4525`, baro, GPS | only flights with pitot tables |
| Performance | GPS, AHRS, howgozit | **engine monitor**, **weather** |

### Planned: engine monitor

Drop exports under `~/kingfisher/engine/raw/`, normalize + time-align to
kingfisher `ts_ns` / UTC, link from sidecar `engine_dump:`.

### Planned: weather

Observations and forecasts (METAR/TAF, winds and temperatures aloft, turbulence
products) under `~/kingfisher/weather/`, keyed by flight time and route/region.
Used for TAS/wind consistency checks and atmosphere context in flight models —
not required for v1 catalog/health.

## Related

- `scripts/analyze_flight_sampling.py` — earlier gap/plot tool; health logic is
  being folded into `analysis/health.py` (legacy script kept for now).
- `docs/timestamps.md` — what `ts_ns` means per source.
