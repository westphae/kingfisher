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

## GPS

The `NEO-M9N` is wired to the Pi 5's GPIO UART (GPIO 14/15, header pins
8/10), enabled by `dtoverlay=uart0-pi5` in `/boot/firmware/config.txt`.

Kingfisher assumes the host wall clock is already sane. For offline operation,
discipline the Pi with `gpsd` + `chrony` first; see `docs/time-sync.md`.

**Pi 5 gotcha:** that UART is `/dev/ttyAMA0`, *not* `/dev/serial0`. On a
Pi 5 `/dev/serial0` points at the dedicated 3-pin debug-UART connector
(`ttyAMA10`), so gpsd must be told to use `/dev/ttyAMA0`. In
`/etc/default/gpsd`:

```
DEVICES="/dev/ttyAMA0"
GPSD_OPTIONS="-n -b -s 115200"
```

The `-b` (read-only) flag is deliberate. If you configured the M9N for 115200 baud
(saved to flash), you must also pass `-s 115200`; with `-b`, gpsd will not autobaud
and otherwise opens the port at 19200, which produces no fix and no TPV output. Left to manage the receiver,
gpsd subscribes a heavy set of UBX messages (NAV-SAT/SIG/POSECEF/…) that
saturate the link and drop the effective fix rate. Read-only keeps the
lean NAV-PVT-only stream the receiver is configured to emit, so we get a
clean 10 Hz.

For time discipline, prefer chrony's SOCK integration over SHM and keep
`chronyd` ahead of `gpsd` in the service order. The repo-owned examples live in
`deploy/time-sync/`. For UART-only serial time, tune the chrony `offset` for
your receiver; do not copy the large `0.9999` placeholder from old gpsd examples
verbatim.

The receiver itself is configured once with `ubxtool` and the settings
are saved to its flash (they survive power-off). With gpsd running:

```bash
export UBXOPTS="-P 32.01"                      # M9N protocol version
ubxtool -z CFG-NAVSPG-DYNMODEL,7,7             # Airborne <2g (vs 0=portable)
ubxtool -z CFG-RATE-MEAS,100,7                 # 10 Hz (100 ms)
ubxtool -z CFG-RATE-NAV,1,7                    # 1 nav solution per measurement
```

To bump the link to 115200 and emit only UBX-NAV-PVT (do this with gpsd
*stopped*, talking to the port directly), set `CFG-UART1-BAUDRATE` to
`115200`, `CFG-MSGOUT-UBX_NAV_PVT_UART1` to `1`, and the other
`CFG-MSGOUT-UBX_NAV_*_UART1` items to `0`. The `,7` layer suffix writes
RAM+BBR+Flash so the config persists. The **Airborne dynamic model is the
most important setting** — the factory "portable" default filters motion
in ways that lag or drop fixes during climbs, descents, and turns.

### Rate (5 / 10 Hz)

The receiver always runs at 10 Hz; the GPS tab in the cockpit UI selects
the **recorded** rate (`rate_hz`, 5 or 10). Because gpsd is read-only,
kingfisher doesn't reconfigure the receiver — it decimates the 10 Hz
stream in software (`internal/gps`), publishing/recording every fix at
10 Hz or every other fix at 5 Hz. The setting persists in
`config.json` under `gps.rate_hz`.

### Timestamps (`ts_ns` vs fix time)

Each row in the `gps` table (and every other sensor table) has **`ts_ns`**: nanoseconds
on the Pi's disciplined wall clock. For GPS, **`ts_ns` is not the fix epoch** — it
is when kingfisher **received** the gpsd report. The receiver's fix time is stored
separately as the **`fix_time_unix_s`** column (typically **~600–700 ms earlier**
than `ts_ns` on this M9N + gpsd setup; that gap is receiver/pipeline lag, not clock
error).

For sensor fusion: align GPS with cabin IMU and pod data on **`ts_ns`**; use
**`fix_time_unix_s`** when you need GNSS solution time. Full per-source semantics
(buffered IIO, pod reconstruction, derived devices, fusion checklist) are in
**[`docs/timestamps.md`](docs/timestamps.md)**.

## Timestamps

All flight DB sensor tables store **`ts_ns`** (nanoseconds, Unix epoch) on the
Pi host wall clock (`CLOCK_REALTIME`), ideally GNSS-disciplined before recording.

| Source | `ts_ns` meaning (summary) |
|--------|---------------------------|
| Buffered IIO (ICM45686, …) | Kernel capture time when `current_timestamp_clock == realtime` |
| Polled IIO | Time when the periodic sysfs read completed |
| GPS | Pi time when the TPV was **received**; fix epoch is **`fix_time_unix_s`** |
| Pod (BMP581, MS4525, MMC5983) | Sample time reconstructed onto Pi clock from pod uptime + `age_us` |
| Derived (AHRS, press_alt, geo, compass) | Time when the value was **computed** (inputs may be slightly older) |

See **[`docs/timestamps.md`](docs/timestamps.md)** for measurement error vs true
sample time, GPS fusion guidance, and implementation references.

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

### Browser terminal (optional)

Kingfisher can expose a full-screen web terminal at **`/terminal`** (footer link
on the cockpit page). It is **disabled by default**; enable in `config.json`:

```json
"terminal": {
  "enabled": true,
  "user": "eric",
  "authorized_keys": [
    "ssh-ed25519 AAAA... kingfisher-terminal"
  ],
  "allow_password": false,
  "session_timeout_min": 480,
  "max_sessions": 2
}
```

**Public-key login (recommended):** put one or more OpenSSH `authorized_keys` lines in
`authorized_keys` and set `user` to the Unix account the shell should run as. The
browser signs a one-time challenge with a private key stored locally (generate in
the terminal UI, or import an unencrypted Ed25519 `id_ed25519`). Your password is
never sent. Set `"allow_password": true` to keep PAM login as a fallback.

**Password login:** when `authorized_keys` is omitted, login uses **Linux PAM**
(same username/password as SSH). The shell runs as the authenticated user when
kingfisher runs as that user; logging in as a different user requires root or
`cap_setuid` on the kingfisher binary.

**Security:** without public-key auth, the default HTTP server sends passwords in
cleartext. Use HTTPS or an SSH tunnel in front of `:8080` if password login is
enabled.

Building on Linux requires **`libpam0g-dev`** for CGO (`apt install libpam0g-dev`).

If you want DB filenames, session start times, and buffered IIO timestamps to be
meaningful immediately after boot, start kingfisher only after GNSS time sync is
healthy. `docs/time-sync.md` includes a `chronyc waitsync` systemd pattern and a
verification checklist. Per-table **`ts_ns`** semantics and sensor-fusion usage
(including GPS **`fix_time_unix_s`**) are documented in **`docs/timestamps.md`**.
