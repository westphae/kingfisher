# Pod firmware

This directory will hold the Rust no_std firmware for the wing-pod
`ESP32-C3-Mini-1` (variant `ESP32-C3FH4`). The pod reads three I²C
sensors and transmits batched readings to the cabin Pi over WiFi UDP.

The source tree is added in Phase 3 of the rollout (see the plan in
`/home/eric/.claude/plans/`). This README describes the hardware build
and the toolchain so the firmware drop-in is fast when the time comes.

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

All three sensors run at 3.3 V and share a single I²C bus. The bus uses
**4.7 kΩ pull-ups to 3V3** on both SDA and SCL — only fit them once for
the whole bus, not per sensor. The MS4525DO breakout typically already
has pull-ups; check before stacking.

| ESP32-C3 pin | Net     | To                                    |
| ------------ | ------- | ------------------------------------- |
| `IO4`        | `SDA`   | MS4525DO, BMP581, MMC5983MA — SDA     |
| `IO5`        | `SCL`   | MS4525DO, BMP581, MMC5983MA — SCL     |
| `IO8`        | `LED`   | Status LED (active-low, with 1 kΩ)    |
| `IO3` (ADC)  | `VBATT` | LiPo cell + ÷2 voltage divider        |
| `3V3`        | `3V3`   | LDO output to all sensors             |
| `GND`        | `GND`   | Common ground                         |
| `IO18`/`IO19`| USB D-/+| USB-C connector for flashing          |
| `EN`         | `EN`    | 10 kΩ pull-up to 3V3, optional button |

Pin choices are firmware-defined; revise them here if the firmware
moves them. `IO4`/`IO5` are the convention used by the Arduino board
support package and most C3 demo boards, so dev-kit jumpers wire up
without surprises.

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

Until proper provisioning is added (v2 of the plan), WiFi SSID / PSK
and the Pi's IP:port are compile-time constants. Edit
`firmware/pod/src/cfg.rs` (in Phase 3) before flashing:

```rust
// Edit these for your aircraft.
pub const WIFI_SSID: &str = "kingfisher-ap";
pub const WIFI_PSK:  &str = "change-me";
pub const PI_ADDR:   &str = "192.168.4.1:47808";   // matches Pi's pod_udp_addr
```

The Pi side defaults to listening on `:47808` (configurable via
`pod_udp_addr` in `~/.config/kingfisher/config.json`).

## Verification

1. **Flash a blink-only build first.** Confirms the chip is alive and
   USB flashing works end-to-end without any radio or I²C dependencies.
2. **Hello over UDP**, no sensors. With the Pi's `kingfisher` running,
   the pod should appear in the registry (`pod` device, three
   sampling_frequency rows) within ~5 s of power-up.
3. **One sensor at a time** (BMP581 → MMC5983MA → MS4525DO). Compare
   readings against a reference (METAR for static, compass app for
   mag, syringe + manometer for airspeed) before integrating the next.

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
