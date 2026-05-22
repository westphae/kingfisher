# Pod firmware

Rust no_std firmware for the wing-pod `ESP32-C3-Mini-1` (variant
`ESP32-C3FH4`). The pod associates to the cabin Pi's WiFi AP and
transmits postcard-encoded `Frame`s (defined in `pod_wire/`) over UDP.

**Status: Phase 3c — all three I²C sensors coded.** Firmware polls each present
sensor at 10 Hz and uplinks `static_*`, `mag_*`, and `airspeed_*` as available.
**No sensor is required** at boot; missing devices are re-probed every 5 s.

## Bill of materials

| Part                          | Role                                            |
| ----------------------------- | ----------------------------------------------- |
| ESP32-C3-Mini-1 (C3FH4)       | MCU + radio                                     |
| MS4525DO-DS5AI001DP           | Airspeed (differential pressure), I²C 0x28      |
| BMP581                        | Static pressure, I²C 0x46 (or 0x47 if SDO tied) |
| MMC5983MA                     | Magnetometer, I²C 0x30                          |
| LiPo cell, 3.7 V nominal      | Power                                           |
| 3.3 V LDO (≥250 mA)           | Regulator, e.g. AP2112-3.3 or MCP1700           |
| 2× 4.7 kΩ                     | I²C pull-ups (SDA / SCL → 3V3)                  |
| Pitot/static plumbing tubing  | Routes airflow to MS4525DO and BMP581           |
| Antenna (chip or U.FL pigtail)| 2.4 GHz                                         |

The ESP32-C3-Mini-1 has an on-module PCB antenna; the variant with the
U.FL connector (`ESP32-C3-MINI-1U`) is preferable for a wing pod since
the module sits inside a (probably composite or sheet-metal) fairing.

## Wiring

BMP581 and MMC5983 run at **3.3 V** on the Qwiic bus. The **MS4525DO-DS5**
variant needs **5 V on VCC** (separate from the ESP 3.3 V); I²C lines still
join the same Qwiic chain (confirm your breakout is 3.3 V I²C-tolerant).
Use **4.7 kΩ pull-ups to 3V3** once on the bus; each Qwiic board may also
ship 2.2 kΩ — cut **I2C** on one board if the bus misbehaves when daisy-chaining.

| ESP32-C3 pin | Net     | To                                    |
| ------------ | ------- | ------------------------------------- |
| `IO5`        | `SDA`   | Qwiic / MS4525DO, BMP581, MMC5983MA  |
| `IO6`        | `SCL`   | Qwiic / MS4525DO, BMP581, MMC5983MA  |

Desk bench with [SparkFun Pro Micro ESP32-C3](https://docs.sparkfun.com/SparkFun_Pro_Micro-ESP32C3/hardware_overview/) + Qwiic cable: firmware uses **IO5/IO6** (matches SparkFun `pins_arduino.h`). A plug-in Qwiic BMP581 (SEN-20170, default **0x47**) needs no extra wiring.

Planned wing pod PCB may move the bus to **IO4/IO5**; update `main.rs` when that layout is fixed.
| `IO8`        | `LED`   | Status LED (active-low, with 1 kΩ)    |
| `IO3` (ADC)  | `VBATT` | LiPo cell + ÷2 voltage divider        |
| `3V3`        | `3V3`   | LDO output to all sensors             |
| `GND`        | `GND`   | Common ground                         |
| `IO18`/`IO19`| USB D-/+| USB-C connector for flashing          |
| `EN`         | `EN`    | 10 kΩ pull-up to 3V3, optional button |

**Bench Qwiic chain:** Pro Micro → BMP581 (0x47) → [MMC5983MA](https://www.sparkfun.com/products/19921) (0x30) → MS4525 (0x28) when powered. Firmware re-probes MS4525 every 5 s if it was missing at boot.

The native USB peripheral on `IO18`/`IO19` lets `espflash` talk
directly to the chip — no external USB-UART bridge needed. A single
USB-C connector with ESD protection is sufficient.

### Pitot plumbing

* MS4525DO has two pressure ports labelled `+` and `-`.
  - `+` → pitot (total) tube on a probe ahead of the wing leading edge
  - `−` → static port on the side of the pod
* BMP581 sits in a chamber connected to the same static port (it is a
  silicon MEMS device — keep it out of the airflow stream).
* Mount the MMC5983MA on the same PCB but as far as possible from
  current-carrying traces and the LiPo, and orient it so its X axis
  is aligned with the airframe longitudinal axis when the pod is
  level. Axis corrections happen on the Pi (see the plan), so an
  approximate mount is fine — just keep it consistent.

### Power & current budget

Steady-state draw with WiFi STA active is ~120 mA average and ~350 mA
peak on transmit. A 1000 mAh LiPo gives ≥ 5 h flight time at 50 Hz
mag / 10 Hz airspeed/static. The LDO must handle the TX peak; AP2112
is OK to 600 mA. Add a 22 µF tantalum + 100 nF ceramic at the LDO
output to keep the radio happy.

A 5 V boost option (so the pod accepts a wider supply range) is fine
to add downstream but doesn't change anything firmware-side.

## Toolchain

ESP32-C3 is a RISC-V 32-bit core (`riscv32imc`). The Rust ESP ecosystem
supports it natively under stable Rust — no nightly or xtensa-llvm
required (that's only the Xtensa-core ESP32 variants).

```bash
# Once per machine:
rustup target add riscv32imc-unknown-none-elf
cargo install espflash
```

`espflash` provides both `cargo espflash` (subcommand) and a standalone
`espflash` binary. It speaks USB-CDC directly to the C3 over the
on-module USB port, so no separate driver/bridge tool is needed on
Linux.

If you also need to read the on-chip serial console while running:

```bash
espflash monitor
```

## Build & flash

Once the source tree exists:

```bash
cd firmware/pod
cargo build --release             # builds the .elf
espflash flash target/riscv32imc-unknown-none-elf/release/pod --monitor
```

The `--monitor` flag stays attached after flash and prints `defmt`
log lines from the running firmware over the same USB connection.

### Pre-shared configuration

Until provisioning lands (v2), pod WiFi and UDP settings live in the
same kingfisher JSON config as the Pi runtime (`~/.config/kingfisher/config.json`).
See [`config.example.json`](../../config.example.json) for the `pod` block shape.

```json
"pod": {
  "wifi_ssid": "kingfisher",
  "wifi_password": "",
  "udp_addr": "192.168.10.1:47808"
}
```

`pod.udp_addr` must be the AP gateway (`ipv4.addresses` on the Pi AP
connection) and the port kingfisher listens on. `build.rs` reads the
file at compile time and injects values into `src/cfg.rs` via `env!`.
Cross-machine builds: `KINGFISHER_CONFIG=/path/to/config.json cargo build --release`.

## Deferred bench validation (Phase 3c / MS4525)

Not required for Phase 4 firmware work. Run when 5 V is available for the
MS4525DO-DS5:

| Step | Action | Pass criteria |
|------|--------|----------------|
| Power | Apply **5 V** to MS4525 VCC (3.3 V I²C on Qwiic) | Serial: `ms4525 attached at 0x28` within 5 s hot-attach |
| Flash | `cargo build --release` + `espflash flash` (FW `0x0003_0002` or later) | `uplink ok` with non-empty batches |
| Pi | `go run ./cmd/kingfisher` (not podprobe on `:47808`) | `pod` device: `static_*`, `mag_*` |
| Airspeed | Rest + light suction/blow on **+** port | `airspeed_dp_pa` ~0 at rest; moves with ΔP; `airspeed_temp_c` plausible |

## Verification

Pre-condition: Pi WiFi AP running; `~/.config/kingfisher/config.json`
`pod` block matches the AP; sensors on Qwiic (Pro Micro **IO5/IO6**).

1. **Flash**: `cargo build --release` then
   `espflash flash --monitor target/.../release/pod`. Monitor should show:
   - `pod: i2c scan: 0x47(id=0x50) … 0x30(id=0x30)` and `0x28(st=0)` when MS4525 powered
   - `pod: sensor board ready …` (ms4525 line optional if no 5 V)
   - WiFi + `sent Hello` + `uplink ok, … pkts`
2. **Kingfisher**: `go run ./cmd/kingfisher` — `pod` shows `static_*`, `mag_*`, and
   `airspeed_*` when MS4525 is on the bus.
3. **MS4525 bench** (5 V applied): `airspeed_dp_pa` near 0 at rest; suction/blow on
   the `+` port moves ΔP. Without 5 V, `ms4525 not present` is expected.

**Status: Phase 4 — control plane and link keepalive.** Firmware answers Pi
`Ping` with `Pong`, gates `Hello` when the link is active (30 s rediscover),
handles `Cmd`/`Ack` (including `SetRate`), sends dynamic caps and periodic
`Status`. Pi assigns cmd seq, tracks pending `Ack`, and reverts failed rate changes.

### Persisted settings (Pi config)

Sampling rates set in the web UI are written to `~/.config/kingfisher/config.json`
under `pod.attrs` (e.g. `in_mag_sampling_frequency`) and survive power cycles.
Kingfisher reapplies
them on startup and after each pod Hello; changing config and saving also re-sends
`SetRate` to the pod. Firmware does not read this file — only the Pi pushes rates
over UDP after restart.

### Sampling rates and autonomic recovery (FW `0x0004_0002+`)

BMP581 uses ~25 ms forced conversions; MMC5983 reads are much faster. The firmware
enforces a **75 ms I²C work budget per 100 ms tick** and rejects unsustainable
combinations at `SetRate` time (Ack `ok=false`). Example: **50 Hz static + 50 Hz
mag is rejected**; **25 + 50** is OK. Hello **max Hz** shrinks when more sensors
are attached so the UI does not offer impossible pairs.

If reads fail repeatedly, a tick overruns (>80 ms of I²C), or many errors occur in
one tick, the pod **backs off** the last changed rate (or halves it) and runs
**I²C recovery** (re-init attached sensors, safe 10 Hz defaults). Serial log:
`pod: backing off …` / `pod: i2c recovery`. No wing visit required for that flight.

### Phase 4 verification

1. Flash FW `0x0004_0002` (or later); start kingfisher on the Pi AP.
2. Serial: `sent Hello` once at boot, then quiet; `uplink ok` when sensors present.
3. Pi log: no repeated `pod: ping` errors after the pod appears; `ack for_seq=…` when
   changing `sampling_frequency` on the pod settings channels (`mag`, `static`, `airspeed`) in the UI.
4. Stop kingfisher for ~30 s: Hello resumes on the pod serial log.
5. Hot-plug a sensor: next Hello lists only attached caps.

## Troubleshooting

* **`espflash` can't find a device**: hold the `BOOT` button while
  plugging USB-C, or pulse `EN` low while `IO9` is held low. If the
  C3 is in deep sleep the USB CDC interface disappears — same fix.
* **I²C reads return all-0xFF**: pull-ups missing or wrong rail; or
  two devices configured to the same address (rare here since all
  three are distinct).
* **WiFi never associates**: the Pi-side AP must be on a 2.4 GHz
  channel — the C3 has no 5 GHz radio. The bench AP also matters: a
  Pi 5 running `hostapd` on `wlan0` is the supported topology.
* **Packets arrive at the Pi but kingfisher never shows the pod**:
  check `tcpdump -i wlan0 udp port 47808` for raw frames. If frames
  are present, the issue is either the CRC (most likely — re-verify
  the postcard schema matches `pod_wire/`) or the kingfisher pod
  listener is bound to a different interface; confirm with `ss -lup`.
