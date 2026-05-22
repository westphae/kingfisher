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
- `internal/derive/{altitude,declination,ahrs}.go` — virtual devices computed from hub snapshots. Each reads `hub.SnapshotNow()` and republishes a synthesized sample.
- `pod_wire/` — shared Rust no_std crate defining the pod ↔ Pi wire format (`postcard` + CRC32 framing). The Go side mirrors it in `internal/pod/wire/`.
- `firmware/pod/` — ESP32-C3 firmware (Phase 4: optional I²C sensors, Cmd/Ack SetRate, Ping/Pong, gated Hello, dynamic caps, Status). Pod sampling rates persist in `config.json` (`pod.attrs`) and are reapplied by `internal/pod` on Pi restart / Hello.
- `datasheets/` — gitignored local working files. Don't expect them in CI.

## go.mod has local replace directives

```
replace github.com/westphae/go-iio => ../go-iio
replace github.com/westphae/goflying => ../goflying
replace github.com/westphae/geomag => ../geomag
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

After any change to types in `pod_wire/src/lib.rs`, regenerate `fixtures.txt` and re-run `go test ./internal/pod/wire/`. The Go test loads those fixtures and decodes them — if they diverge, the wire contract is broken.

## Data flow contract

`live.Sample` is the universal data unit: `{Device string, TsNs int64, Values map[string]float64}`. Every source produces it, every sink consumes it.

- Local IIO sensors are polled by per-device goroutines in `sensors.runOne` at the device's `SampleHz` and `time.Now()`-stamped at read time.
- Out-of-process sources (gps, pod) are push-driven: they receive an external event and publish directly to `hub`. Do **not** wire push sources through `sensors.runOne` — it overstamps timestamps with receive time, which corrupts cross-stream alignment.
- The pod ingest in particular stamps `TsNs` to the *original sample time* on the pod, via an EMA offset between pod uptime and Pi wall time. See `internal/pod/pod.go::onBatch`.

The `live.Hub` keeps only the latest sample per device. If you publish a sample with a partial `Values` map, fields not in the map are lost from `hub.latest`. For multi-channel push sources, maintain a sticky cache server-side and republish the full map every sample (see `internal/pod/reader.go::snapshotValues`).

## sensors.Reader as a façade

The pod has no sysfs. `podReader` satisfies `sensors.Reader` purely so `sensors.Registry.WriteAttr` works for `/api/devices/pod/attrs`. `SetChannelAttr` is fire-and-forget (translates to a `wire.Cmd` enqueued on an outbound channel); the data path goes hub-side directly. When adding a new push source, follow this split: data goes via hub, control surface optionally hangs off the Registry via a `Reader` façade.

## The pod plan

The current pod work is rolled out in phases (Phase 0 = wire crate, Phase 2 = Pi-side ingest, Phase 3 = I²C sensors, Phase 4 = control plane + link keepalive are done; Phase 5 = AHRS mag integration is pending). Wire `Cmd` frames carry a Pi-assigned `seq` echoed in `Ack.for_seq`. Phase 5 explicitly requires surgery on `internal/derive/ahrs.go::findIMU` — it currently expects accel/gyro/mag on the *same* device sample, which won't match the pod-vs-IMU split.

Plan files live under `~/.claude/plans/` outside the repo.

## Project conventions worth knowing

- Channel names in `Values` maps are the SQLite column names (after `store.Sanitize`). Use SI-friendly names with unit suffixes where helpful: `pressure_pa`, `temp_c`, `airspeed_dp_pa`, `mag_x_ut`. Angles on derived devices are plain `roll`/`pitch`/`yaw` (degrees). See `internal/units`.
- Virtual devices that synthesize data publish under a stable name (`gps`, `pod`, `ahrs`, `press_alt`, `geo`). Adding a new one only requires `hub.Publish` calls and (optionally) `Registry` registration.
- `chips.go` holds per-chip fallback attr tables. Don't add fallback entries for the pod — its caps are advertised at runtime via `wire.Hello`.

## Git / release etiquette

There is a global rule (in `~/.claude/CLAUDE.md`) that no `git push`, no `git tag`, no GitHub release creation, and no version-string bumps happen without explicit per-action approval. CI being green is not authorization. Local commits, branches, and tests are fine. Show a proposed commit message before committing unless explicitly told to commit.

**Atomic commits:** When committing, split work into logical units (e.g. a new driver, then optional bring-up, then docs/plan notes)—not one large commit for everything. Stage and commit each chunk with its own message; dependencies first.
