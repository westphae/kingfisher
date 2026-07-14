# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Kingfisher is a Go flight data recorder for a turbonormalized Beech Bonanza V35B. It runs on a Raspberry Pi 5 in the cabin, reads IIO sensors and a uBlox GPS via gpsd, optionally ingests a wing-mounted "pod" (ESP32-C3 over UDP), derives AHRS / pressure altitude / declination, persists everything to a per-flight SQLite DB, and serves a live cockpit UI over HTTP/WebSocket.

The Pi this repo lives on is also the *deployment target*. Tests can hit real sysfs / network paths; that is expected, not test smell.

## Repository layout (non-obvious parts only)

- `cmd/kingfisher/main.go` — orchestrator; composes every long-running goroutine. New data sources are wired in here.
- `internal/live/hub.go` — central pub/sub bus carrying `live.Sample` structs. Every data source publishes here; every consumer (web WS, store buffer, derive pkgs) subscribes here. This is the architectural pivot point.
- `internal/sensors/` — IIO discovery + per-device polling. The `Reader` interface defined here is the contract; `Registry` is what the web `/api/devices/*/attrs` endpoint talks to.
- `internal/gps/gpsd.go` — out-of-process TCP source publishing under virtual device name `gps`. The canonical pattern for any non-IIO data source.
- `internal/pod/` — wing-pod ingest over UDP. Mirrors the `gps` shape but adds a `podReader` registered with `sensors.Registry` so the existing attr UI works. Data path bypasses `sensors.runOne` — Reader is a façade for the UI only.
- `internal/web/terminal/` — optional browser shell at `/terminal` (SSH pubkey challenge login and/or PAM password, WebSocket PTY). Disabled unless `terminal.enabled` in config.
- `internal/howgozit/` — in-flight manual log persistence (`howgozit_log` registry + `hgz_*` data tables with `ts_ns`). Templates in `config.json` (`howgozit.templates`) seed new logs via **+ Log**; flight logs are per-DB only (empty until the pilot adds one). **Edit** adjusts log name and column schema; **→ Template** saves schema to config. REST API under `/api/howgozit/*`.
- `internal/derive/{altitude,declination,ahrs,compass,airspeed}.go` — virtual devices computed from hub snapshots. `compass` runs magkal's per-axis EKF on a selectable magnetometer, compares measured field to WMM `geo`, and publishes heading after align (manual or GPS track while taxiing 2–40 kt). For **pod mag + cabin IMU**, use `compass.align_method: "wmm"`: align maps pod `magCal` to the WMM field in the vehicle frame (cabin AHRS/accel attitude + magnetic heading). After align, measured `field_*_nt` / `inclination` use **WMM NED direction** (`geo`) with **magnitude** from the aligned mag so the compare table matches `geo` when calibration is good. Single-IMU setups keep `"accel"` (gravity + mag). Optional `pod_mount_r` is a fixed pod→fuselage rotation when the pod axis frame ≠ cabin (identity when unset). **`airspeed`** derives IAS/TAS from pod MS4525 ΔP plus static P/OAT (BMP581 preferred); custom UI tab `airspeed (calc)`.
- `pod_wire/` — shared Rust no_std crate defining the pod ↔ Pi wire format (`postcard` + CRC32 framing). The Go side mirrors it in `internal/pod/wire/`.
- `firmware/pod/` — ESP32-C3 firmware (Phase 4: optional I²C sensors, Cmd/Ack SetRate, Ping/Pong, gated Hello, dynamic caps, Status). Pod sampling rates persist in `config.json` (`pod.attrs`) and are reapplied by `internal/pod` on Pi restart / Hello. **Power**: three-stage battery protocol (Active / Burst = radio-off collect + periodic drain / Protect = deep sleep at LiPo floor) — see `docs/pod-power.md`. `pod.modem_power_save` is on in the aircraft config (bench-verified; an earlier "brcmfmac drops unicast to dozing STA" diagnosis was actually a firmware Ping/keepalive bug, fixed).
- `datasheets/` — gitignored local working files. Don't expect them in CI.

## go.mod has local replace directives

```
replace github.com/westphae/go-iio => ../go-iio
replace github.com/westphae/goflying => ../goflying
replace github.com/westphae/geomag => ../geomag
replace github.com/westphae/magkal => ../magkal
```

These resolve to sibling directories at `$GOPATH/src/github.com/westphae/{go-iio,goflying,geomag}`. If you see import errors, those siblings are probably missing or unbuilt — fix the checkout, don't drop the replace.

## Commands

```bash
# Build & run everything (Go)
go build ./...
go test ./...
go test -count=1 -run TestUDPIntegration ./internal/pod/    # one test
go run ./cmd/kingfisher                                      # run on this Pi

# Rust side (pod_wire crate)
source ~/.cargo/env
cd pod_wire && cargo test                                    # unit tests
cargo run --example pod_wire_dump > ../internal/pod/wire/testdata/fixtures.txt
                                                             # regenerate cross-language fixtures

# Pod firmware (reads ~/.config/kingfisher/config.json pod section at build time)
cd firmware/pod && cargo build --release
```

After any change to types in `pod_wire/src/lib.rs`, regenerate `fixtures.txt` and re-run `go test ./internal/pod/wire/`. The Go test loads those fixtures and decodes them — if they diverge, the wire contract is broken. `scripts/check-wire.sh` enforces this: it regenerates to a temp file and diffs against the committed fixtures (exit 1 on drift), and `scripts/check-wire.sh --write` regenerates in place. Wire it into CI / a pre-commit hook so the honour-system regen can't be forgotten.

## Data flow contract

`live.Sample` is the universal data unit: `{Device string, TsNs int64, Values map[string]float64}`. Every source produces it, every sink consumes it. **`docs/timestamps.md`** is the canonical reference for what `TsNs` means per table, how it relates to true measurement time, and GPS **`fix_time_unix_s`** vs row time.

- Local IIO sensors use kernel **buffered capture** (hrtimer trigger + `/dev/iio:deviceN` FIFO) when the device exposes `scan_elements`; timestamps come from the buffer `timestamp` channel when `current_timestamp_clock` is `realtime`. Set `"use_buffer": false` in config for the legacy sysfs poll path (`sensors.runOne`), which stamps `TsNs` at read completion instead. Requires `iio-trig-hrtimer` and configfs access (see `go-iio` `EnsureHRTimer`).
- GPS keeps the fix epoch in `gps.Fix.Time` and the **`fix_time_unix_s`** DB column, but emitted `gps` sample **`TsNs`** is Pi wall clock at TPV receive so all stored streams share the same `CLOCK_REALTIME` base after GNSS discipline. Expect **`TsNs − fix_time_unix_s ≈ 600–700 ms`** on the M9N/gpsd path (pipeline lag, not clock error).
- Out-of-process sources (gps, pod) are push-driven: they receive an external event and publish directly to `hub`. Do **not** wire push sources through `sensors.runOne` — it overwrites source timestamps with receive time, which corrupts cross-stream alignment.
- The pod ingest stamps `TsNs` from each reading's `age_us` and pod uptime, mapped into Pi wall clock via the EMA offset in `internal/pod/pod.go::onBatch`.
- Derived devices (`ahrs`, `press_alt`, `geo`, `compass`, `airspeed`) stamp `TsNs` at compute time (`time.Now()`), not at the timestamp of their input samples — use source device rows for tight fusion.
- Telemetry devices use chip names from pod `Hello` (e.g. `bmp581`, `mmc5983`, `ms4525`, `bq27441`); `location` is `hub` (cabin IIO) or `pod` (wing) in UI and `sensor_attrs`. Legacy aggregate hub device `pod` is hidden from tabs.
- **Battery Babysitter** (`bq27441` tab + header Batt line): voltage, current, power, remaining/full capacity, SOC, and computed time-to-empty at ~1 Hz via wire proto v3 `Reading::Battery`. Status frames still carry `battery_v` for link health.
- BMP581 on the pod uses NORMAL+FIFO; drained frames are queued and uplinked with distinct `age_us` per sample (up to `MAX_READINGS` per datagram).

The `live.Hub` keeps only the latest sample per device. Pod ingest publishes sparse per-sensor maps to each `pod_*` device.

## sensors.Reader as a façade

The pod has no sysfs. `podReader` satisfies `sensors.Reader` purely so `sensors.Registry.WriteAttr` works for `/api/devices/pod/attrs`. `SetChannelAttr` is fire-and-forget (translates to a `wire.Cmd` enqueued on an outbound channel); the data path goes hub-side directly. When adding a new push source, follow this split: data goes via hub, control surface optionally hangs off the Registry via a `Reader` façade.

## The pod plan

The current pod work is rolled out in phases (Phase 0 = wire crate, Phase 2 = Pi-side ingest, Phase 3 = I²C sensors, Phase 4 = control plane + link keepalive are done; Phase 5 = AHRS mag integration is pending). Wire `Cmd` frames carry a Pi-assigned `seq` echoed in `Ack.for_seq`. Phase 5 explicitly requires surgery on `internal/derive/ahrs.go::findIMU` — it currently expects accel/gyro/mag on the *same* device sample, which won't match the pod-vs-IMU split.

Plan files live under `~/.claude/plans/` outside the repo.

## Project conventions worth knowing

- Channel names in `Values` maps are the SQLite column names (after `store.Sanitize`). Use SI-friendly names with unit suffixes where helpful: `pressure_pa`, `temp_c`, `airspeed_dp_pa`, `mag_x_ut`. Angles on derived devices are plain `roll`/`pitch`/`yaw` (degrees). See `internal/units`.
- Virtual devices that synthesize data publish under a stable name (`gps`, `pod`, `ahrs`, `press_alt`, `geo`, `compass`, `airspeed`). Adding a new one only requires `hub.Publish` calls and (optionally) `Registry` registration. The cockpit **compass** tab uses a custom panel (`static/compass.js`): SVG rose (needle length ∝ H), digital heading, model-vs-measured table alongside `geo`, and align method **WMM** vs **accel** in settings. **Airspeed (calc)** uses `static/airspeed.js`: hero IAS/TAS plus raw ΔP/static P/temp when MS4525 is connected. **Howgozit** (`static/howgozit.js`) is a fourth bottom-nav section for pilot-entered logs (ATC frequency changes, flight/engine conditions, etc.) stored in the current flight DB with `ts_ns` for post-flight correlation with sensor tables — not a hub device. Toolbar **Edit** renames the log and manages columns; **→ Template** writes the current schema to `config.json`.
- `chips.go` holds per-chip fallback attr tables. Don't add fallback entries for the pod — its caps are advertised at runtime via `wire.Hello`.
- Flight DB: `_session` is written at startup; `metadata` records startup clock keys (`clock_startup_state`, GPS probe, and chrony/PPS snapshot via `clock.StartupMeta`). See `docs/timestamps.md`. In-flight **`clock_nudge_*`** metadata rows log auto/manual `chronyc reselect` attempts (`internal/clock/nudger.go`). Cockpit status drawer: auto-retry when unsynced, manual **Retry sync** / **Restart time services** (`clock.resync_helper` + sudo — `deploy/time-sync/verify.md` §6). `sensor_attrs` includes a `location` column (`hub`/`pod`) and logs attrs for all devices at startup/reload (`internal/pod/TODO.md` for future Hello chip-attr snapshot + `SetAttr` registers).

## Driver TODOs (sibling repos)

- **[icm20948-mod](../icm20948-mod/TODO.md)** — Implement on-chip **hardware FIFO** (today: hrtimer + one SMBus burst per sample; mag aux ~100 Hz cap). Kingfisher `MaxBufferedHz` reflects that limit until FIFO lands.
- **[icm45686-mod](../icm45686-mod/TODO.md)** — INT1 is mandatory in DT; Pi overlay often needs `dtoverlay=icm45686,int_trigger=8` (level-low) for active-low INT1. Kingfisher uses **hwfifo** on `icm45686-gyro` / `icm45686-accel`, syncs `sampling_frequency` to `sample_hz`, skips `buffer/data_available` polling, and restarts the buffer on stall before polled fallback.

## Git / release etiquette

There is a global rule (in `~/.claude/CLAUDE.md`) that no `git push`, no `git tag`, no GitHub release creation, and no version-string bumps happen without explicit per-action approval. CI being green is not authorization. Local commits, branches, and tests are fine. Show a proposed commit message before committing unless explicitly told to commit.

**Atomic commits:** When committing, split work into logical units (e.g. a new driver, then optional bring-up, then docs/plan notes)—not one large commit for everything. Stage and commit each chunk with its own message; dependencies first.
