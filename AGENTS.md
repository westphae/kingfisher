# AGENTS.md

## Cursor Cloud specific instructions

### Sibling Go modules

`go.mod` has three `replace` directives pointing to `../go-iio`, `../goflying`, and `../geomag`. The update script clones these from `github.com/westphae/` into the parent of `/workspace` if missing. They are shallow clones; if you need history, deepen them.

### Running the app

```bash
go run ./cmd/kingfisher
```

The app starts the HTTP/WS server on `:8080` by default. It gracefully degrades without:
- **IIO sensors** — logs "sensors: 0 device(s) discovered" and continues (no hardware in the VM).
- **gpsd** — retries connection every 2s; GPS-derived data (declination, position) will be unavailable.
- **Wing pod** — logs a message if no UDP peer arrives; pod data will be unavailable.

AHRS will spam "IMU-measured acceleration was zero" when no IMU hardware is present — this is expected in the VM.

### Key API endpoints (for testing)

See `CLAUDE.md` § Commands for build/test commands. API routes are documented at the top of `internal/web/server.go`:
- `GET /api/status` — health/state check
- `GET/POST /api/config` — read/write config
- `GET/POST /api/recording` — pause/resume recording
- `GET /api/devices` — list IIO devices (empty in VM)

### Rust side

`pod_wire/` is a standalone Rust crate. Run `cargo test` inside it. The ESP32-C3 firmware under `firmware/pod/` requires a cross-compilation target (`riscv32imc-unknown-none-elf`) and is not buildable in the VM without additional toolchain setup — this is optional for most development work.
