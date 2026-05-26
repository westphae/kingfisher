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

**Pi 5 gotcha:** that UART is `/dev/ttyAMA0`, *not* `/dev/serial0`. On a
Pi 5 `/dev/serial0` points at the dedicated 3-pin debug-UART connector
(`ttyAMA10`), so gpsd must be told to use `/dev/ttyAMA0`. In
`/etc/default/gpsd`:

```
DEVICES="/dev/ttyAMA0"
GPSD_OPTIONS="-n -b"
```

The `-b` (read-only) flag is deliberate. Left to manage the receiver,
gpsd subscribes a heavy set of UBX messages (NAV-SAT/SIG/POSECEF/…) that
saturate the link and drop the effective fix rate. Read-only keeps the
lean NAV-PVT-only stream the receiver is configured to emit, so we get a
clean 10 Hz.

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
