# Timestamps in flight databases

Every sensor table in a kingfisher flight DB has a **`ts_ns`** column: nanoseconds
since the Unix epoch. Values are written exactly as each producer sets
`live.Sample.TsNs` — the store does not restamp rows at flush time.

All streams are intended to share one time base: the Pi host **`CLOCK_REALTIME`**
wall clock, ideally GNSS-disciplined via chrony (and PPS when wired). See
`docs/time-sync.md` for bringing that clock up before recording.

This document explains what `ts_ns` means per source, how close it is to the
**actual measurement instant**, and how to use timestamps for later sensor
fusion — especially GPS.

## Quick reference

| Table / source | What `ts_ns` represents | vs actual measurement | Notes |
|----------------|-------------------------|------------------------|-------|
| Buffered IIO (cabin) | Kernel buffer capture time | Best case: sample time on Pi clock; small FIFO/read delay | Preferred path when `current_timestamp_clock == realtime` |
| Polled IIO (`use_buffer: false`) | `time.Now()` when sysfs read finishes | Earlier by up to one poll period + bus latency | Legacy / fallback |
| **`gps`** | Pi wall clock when gpsd TPV is **received** | Fix computed ~600–700 ms earlier (see below) | Fix epoch stored separately as **`fix_time_unix_s`** |
| Pod sensors (`bmp581`, `mmc5983`, `ms4525`, …) | Sample time reconstructed on Pi clock | Pod `uptime − age_us`, mapped via EMA sync | UDP / batch jitter adds uncertainty |
| Derived (`ahrs`, `press_alt`, `geo`, `compass`) | `time.Now()` when value is **computed** | Inputs may be tens of ms older | Not the timestamp of fused inputs |
| **Howgozit** (`hgz_*` tables) | `time.Now()` when the pilot taps **+** (editable) | User may backdate via time cell | Manual annotations; join to sensors on `ts_ns` |
| `sensor_attrs` | When the attr snapshot was logged | N/A (configuration, not a sample) | |
| `_session.start_time`, DB filename | Pi wall clock at session open | N/A | |

## Cabin IIO (buffered capture)

When a device uses the kernel IIO buffer (`scan_elements` present, default path
in `internal/sensors/buffer.go`):

1. If `current_timestamp_clock` is **`realtime`**, `ts_ns` is the kernel's
   timestamp for that buffer frame — the Pi wall clock at capture, which is the
   closest thing to true sample time for IMU, baro, etc. on the Pi.
2. Otherwise kingfisher falls back to the buffer's raw `timestamp_ns` channel (if
   any), or finally `time.Now()` when the frame is read.

Typical residual error vs the chip's physical sample instant:

- Trigger/FIFO depth and batch read latency (often **sub‑ms to a few ms** at
  100 Hz+).
- Any host clock slewing from chrony (usually negligible with PPS).

Implementation: `internal/sensors/buffer.go::sampleTimeNs`.

## Cabin IIO (polled / legacy)

With `"use_buffer": false`, `sensors.runOne` sets `ts_ns` to **`time.Now()`**
when the periodic sysfs read completes (`internal/sensors/sensors.go`).

The measurement happened **before** that instant — by roughly half a poll
interval on average, plus I²C/SPI transfer time. At 10 Hz, expect on the order
of **tens of milliseconds** of ambiguity vs true sample time.

## GPS (`gps` table)

GPS is the most important special case.

### Two different times in each row

| Field | Meaning |
|-------|---------|
| **`ts_ns`** | Pi wall clock when kingfisher **received** the gpsd TPV report (`time.Now()` at ingest). Use this to align GPS rows with cabin IIO, pod, and derived streams on the **same Pi timeline**. |
| **`fix_time_unix_s`** (value column) | GNSS **fix epoch** from the receiver, as reported by gpsd in the TPV. Use this when you care about **when the navigation solution is valid in GPS time** — not for direct join to IMU `ts_ns` without accounting for the gap below. |

Implementation: `internal/gps/gpsd.go::onTPV`.

### Why `ts_ns − fix_time_unix_s ≈ +600–700 ms`

On the M9N with lean UBX-NAV-PVT over gpsd (read-only, 10 Hz), the fix epoch
in each TPV is typically **600–700 ms earlier** than `ts_ns`. That is **not**
a broken Pi clock and **not** fixed by PPS:

- **PPS + chrony** discipline the Pi wall clock to sub‑millisecond accuracy.
- **`fix_time_unix_s`** still reflects the receiver/gpsd navigation pipeline
  (solution time tag, binary stream, gpsd TPV generation).

Wire latency (UART at 115200) is only milliseconds; changing USB vs GPIO UART
does not remove this gap. Kingfisher records both timestamps so downstream
fusion can choose the right one.

### GPS and sensor fusion

- **Fuse GPS position/velocity with cabin IMU on one timeline:** use **`ts_ns`**
  for GPS rows and IMU rows (nearest-neighbor or interpolation in Pi time).
- **Analyze position vs GNSS solution time:** use **`fix_time_unix_s`**, not
  `ts_ns`.
- **Do not assume** `ts_ns` equals fix time. If you must relate them, use the
  recorded **`fix_time_unix_s`** column (or a per-flight median of
  `ts_ns − fix_time_unix_s` as a sanity check, not as a substitute for the
  column).

Recorded rate (`gps.rate_hz` 5 or 10) decimates the 10 Hz receiver stream in
software; both `ts_ns` and `fix_time_unix_s` come from the same TPV for each
kept row.

## Pod (wing sensors)

Pod readings arrive in UDP batches. For each sample (`internal/pod/pod.go::onBatch`):

```text
ts_ns ≈ (pod_uptime_ns − age_us) + EMA_offset_to_Pi_wall
```

So `ts_ns` is the **estimated measurement instant on the Pi clock**, not UDP
receive time (except before the EMA time sync converges).

Residual error vs true wing-side sample time:

- Pod free-running clock vs Pi (EMA smoothed, not PPS on the pod).
- **`age_us`** per reading (good for samples in a multi-reading batch).
- Radio/UDP jitter and batching (usually **ms** scale unless the link is lossy).

Pod tables use chip names (`bmp581`, `mmc5983`, `ms4525`, …), same `ts_ns`
semantics per reading.

## Derived virtual devices

`ahrs`, `press_alt`, `geo`, and `compass` set `ts_ns` to **`time.Now()`** when
the derivation runs (`internal/derive/*.go`). They read the latest hub snapshot
at that moment; input samples may be slightly older.

For tight fusion, prefer **source** device timestamps (IMU, GPS, mag, baro)
rather than derived `ts_ns`. Derived rows are convenient for logging and UI, not
a substitute for input-sample time alignment.

## Other database timestamps

- **`sensor_attrs.ts_ns`** — when attributes were snapshotted (startup, reload,
  or attr change), not sensor physics time.
- **`_session.start_time`** and the **DB filename** — Pi wall clock at open;
  start kingfisher after chrony is healthy if cold-boot naming accuracy matters.

## Flight DB metadata (`metadata` table)

At DB open, kingfisher writes startup clock keys (Pi wall vs GPS fix epoch probe
plus chrony/PPS discipline snapshot):

| Key | Meaning |
|-----|---------|
| `clock_startup_state` | GPS cross-check state (`aligned`, `offset_high`, …) |
| `clock_startup_fallback` | `true` if startup did not pass the GPS discipline check |
| `clock_startup_reason` | Human-readable probe outcome (when set) |
| `clock_startup_offset_ms` | Pi recv time minus fix epoch at probe (when fix present) |
| `clock_startup_pps_present` | `/dev/pps0` existed at open |
| `clock_startup_pps_steering` | chrony active source was PPS (`#* PPS`) |
| `clock_startup_chrony_available` | `chronyc` responded |
| `clock_startup_chrony_synced` | chrony reported a valid reference |
| `clock_startup_chrony_source` | Classified source: `pps`, `gps`, `ntp`, `local`, `unknown` |
| `clock_startup_chrony_source_label` | Raw chrony ref id label (e.g. `PPS`, `GPS`) |
| `clock_startup_chrony_stratum` | Stratum when available |

These are **session snapshots**, not time series. PPS/chrony state during the
flight is not logged row-by-row; use post-flight `chronyc` logs or the cockpit
header for live discipline.

## Practical fusion checklist

1. **One clock** — Treat all `ts_ns` values as Pi `CLOCK_REALTIME` (disciplined
   when chrony/PPS is configured).
2. **Align streams in Pi time** — Join IMU, pod, and GPS on **`ts_ns`** (GPS
   included).
3. **GPS fix epoch** — Use column **`fix_time_unix_s`**, not `ts_ns`, when the
   question is GNSS solution time.
4. **Expect ~650 ms** between GPS `ts_ns` and `fix_time_unix_s` on this
   hardware; that is pipeline lag, not an error to “correct” into IMU time.
5. **Derived AHRS/compass** — Timestamp at compute; for Kalman filters, feed
   raw IMU/GPS/mag with their own `ts_ns`.
6. **Pod vs cabin IMU** — Both on Pi clock after mapping; allow small offset
   and interpolate; pod adds link jitter.

## Howgozit manual logs

Pilot-entered rows live in per-log tables named `hgz_<log_id>` (registry in
`howgozit_log`). Each row has **`ts_ns`** set at row creation (Pi wall clock,
UTC-aligned) and custom REAL/TEXT columns from templates in `config.json`
(`howgozit`). The cockpit time field is **UTC** entered as **HHMM** or
**HHMMSS** (no colon); new rows prepopulate with the current UTC time in that
format.

To annotate sensor data with a manual entry, join on Pi time — for example,
nearest sensor sample within a window:

```sql
SELECT s.*, h.*
FROM icm20948 s
JOIN hgz_flight_conditions h
  ON ABS(s.ts_ns - h.ts_ns) < 500000000;  -- 500 ms
```

Schema snapshots are stored in `howgozit_log.schema_json` so exported DBs
remain self-describing if templates change later.

## Related docs

- `docs/time-sync.md` — gpsd, chrony, PPS, Pi clock discipline
- `README.md` — GPS hardware and rate settings
- `CLAUDE.md` — producer implementation map (`live.Sample` contract)
