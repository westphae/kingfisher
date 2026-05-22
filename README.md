# Kingfisher

Kingfisher is a flight data recorder for light aircraft. It runs on a
Raspberry Pi mounted in the cabin, reads sensors over IIO and ancillary
links, derives quantities like pressure altitude, magnetic declination,
and an AHRS-based attitude estimate, persists everything to a SQLite
flight database, and exposes a live cockpit UI over HTTP/WebSocket.

## Hardware

* **Main unit** mounted near the aircraft CG, inside the cabin.
  * Main controller is a `Raspberry Pi 5`.
  * Accel/gyro is a `ICM45686`.
  * GPS is a `NEO-M9N` attached via UART.
* **Pod unit** mounted out under the wing.
  * Pod controller is an `ESP32-C3-Mini-1`, variant `ESP32-C3FH4`.
  * Airspeed sensor is a `MS4525DO-DS5AI001DP`.
  * Static pressure (altimeter) sensor is a `BMP581`.
  * Magnetometer (compass) sensor is a `MMC5983MA`.

The pod is battery-powered and transmits sensor data to the main unit
wirelessly. See `internal/pod/` (Pi side) and `firmware/pod/` (ESP32
side) for the implementation.

## Building

```
go build ./cmd/kingfisher
```

Pod firmware lives under `firmware/pod/`; see
[`firmware/pod/README.md`](firmware/pod/README.md) for the wiring,
toolchain, and flashing steps.

## Running

```
./kingfisher
```

The cockpit UI is served on `:8080` by default. Configuration lives in
`~/.config/kingfisher/config.json`.

## Linting and formatting

Both languages are linted from a single `Makefile`:

```
make tools      # fetch golangci-lint into ./.bin/ (one-time)
make lint       # Go (golangci-lint) + Rust (cargo fmt --check + clippy)
make fmt        # auto-format Go (gofmt+goimports) and Rust (rustfmt)
make fmt-check  # verify formatting without writing
```

Go rules live in `.golangci.yml`. The Rust side relies on the default
`rustfmt` profile and `cargo clippy -- -D warnings`. The wing-pod
firmware (`firmware/pod/`) targets `riscv32imc-unknown-none-elf` and
needs the esp toolchain to clippy; run `make lint-firmware` from a host
that has it installed.
