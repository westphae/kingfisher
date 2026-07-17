# X1200 UPS HAT — bring-up & verification

The Geekworm X1200 (2×18650, 1S2P) powers the Pi 5 via pogo pins and exposes:

- **MAX17040 fuel gauge** — I²C bus 1 @ `0x36` (cell voltage + modeled SOC; no
  current sense). Bus 1 is shared with the icm45686 IMU at `0x68` (kernel
  driver) — userspace must never address 0x68.
- **Power-loss detect** — GPIO6, input: 1 = external power present, 0 = lost.
- **Charge control** — GPIO16: drive high = disable charging. Kingfisher
  deliberately **never claims GPIO16**; unclaimed (Pi pull-down) it stays at
  the board default, charging enabled.

Policy: run-to-floor. On power loss the recorder keeps recording and powers
off cleanly (flush + WAL checkpoint, then the poweroff sudo helper — see
`deploy/poweroff/verify.md`) only when a debounced floor is reached:
SOC ≤ 10% (on battery) or cell ≤ 3.20 V (any state). Optional
`shutdown_after_s` ride timer, off by default.

## 1. One-time host setup

```sh
sudo modprobe i2c-dev
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf
ls -l /dev/i2c-1        # root:i2c — the service user must be in group i2c
```

`dtparam=i2c_arm=on` must be set in `/boot/firmware/config.txt` (it already
is on kingfisher). The service user also needs group `gpio` for
`/dev/gpiochip0`.

## 2. Hardware sanity (HAT attached)

```sh
i2cdetect -y 1                 # 36 present, 68 shows UU (kernel-owned IMU)
i2cget -y 1 0x36 0x08 w        # VERSION: 0x0200 byte-swapped -> 0x0002
i2cget -y 1 0x36 0x02 w        # VCELL: swap bytes, ×1.25/1000/16 -> volts
i2cget -y 1 0x36 0x04 w        # SOC:   swap bytes, /256 -> percent
gpioget --chip gpiochip0 --bias=pull-up 6    # "active" with power present
```

(`i2cget -w` words are SMBus little-endian; kingfisher's raw reads are
MSB-first and need no swap.)

## 3. Enable

`~/.config/kingfisher/config.json`:

```json
"ups": { "enabled": true }
```

Defaults: 1 Hz poll, SOC floor 10%, voltage floor 3.20 V, ride timer off.
Restart the service; journal shows `ups: MAX17040 present (version 0x0002)`.

## 4. Verify

- `curl -s localhost/api/status | jq .ups` → `present: true, ac_ok: true`.
- Header shows the `⚡ NN%` chip; `ups` device tab lists live values.
- `sqlite3 <flight.db> "SELECT * FROM ups ORDER BY ts_ns DESC LIMIT 3"`.

## 5. Bench power-loss test

Pull the X1200's USB-C input (batteries installed):

- Chip flips to `🔋 NN%`; `ac_ok=0` rows land in the DB; after ~2 min of
  measurable discharge an estimated time remaining appears.
- Re-plug: chip returns to `⚡`, on-battery timer resets.
- To exercise the shutdown path without draining the pack: temporarily set
  `"shutdown_soc_pct"` just below the current SOC, pull input, wait 3 polls →
  clean shutdown, then poweroff. On repower check the breadcrumb:
  `SELECT value FROM metadata WHERE key='ups_shutdown'`.

## Notes

- The X1200 auto-powers the Pi when input power appears, and drops its output
  a few seconds after it stops detecting a running Pi. Verify on the bench
  what it does after `systemctl poweroff` on this board revision; if it holds
  5 V, use the HAT's switch for storage so the cells don't drain.
- Never power the Pi through its own USB-C while the HAT is attached; use
  unprotected flat-top 18650s per Geekworm's guidance.
- Charging at cabin temps above ~45 °C is outside Li-ion charge spec — mind
  hot-day ground soaks.
