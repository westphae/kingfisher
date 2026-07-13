# Kingfisher build playbook — Raspberry Pi 5 + NVMe SSD

Provisions a kingfisher flight-data-recorder host **from scratch**: a Raspberry Pi 5
running RPi OS Lite (trixie, arm64, kernel `6.18.34+rpt-rpi-2712`) on an NVMe SSD,
hostname `kingfisher`. It captures the full delta from a stock image — packages, boot
config, out-of-tree drivers, time sync, networking, and the app — so a fresh build is
reproducible end to end.

First distilled 2026-07-12; keep it current as the system evolves (etckeeper tracks
`/etc`, so most drift is self-documenting). Sections marked **[DISCUSS]** are collected
at the end under *Cruft* (deliberately not installed) and *Improvements*.

---

## Phase 0 — what you supply

Have these ready before you start (from a backup, a password manager, or created fresh).
None are reconstructable by the OS install:

| What | Where it goes | Notes |
|---|---|---|
| Wi-Fi client PSK | `/etc/NetworkManager/system-connections/*.nmconnection` | home-LAN uplink on wlan1 (Phase 10) |
| App config | `~/.config/kingfisher/config.json` | aircraft, pod, GDL90 clients, trusted devices |
| SSH `authorized_keys` | `~/.ssh/authorized_keys` | your key access |
| Flight data (optional) | `~/kingfisher/flights/` | restore from backup, or start empty |
| Go workspace | `~/go/src/github.com/westphae/*` | or re-clone from GitHub (Phase 12) |

Not needed: the NEO-M9N GPS keeps its config in its own flash — only re-run the `ubxtool`
sequence if it's ever factory-reset (DYNMODEL=7 Airborne <2g, 10 Hz, UART1 115200,
UBX-NAV-PVT only, saved to layer mask 7). SSH host keys and Caddy's internal CA are
generated on first run (expect one known_hosts prompt; tablets re-trust HTTPS once).

## Phase 1 — flash and first boot

1. Flash **Raspberry Pi OS Lite (64-bit, trixie)** to the SSD.
2. On the bootfs (FAT) partition before first boot:
   - `touch ssh`
   - `userconf.txt`: `eric:$(echo 'yourpassword' | openssl passwd -6 -stdin)`
     (a fresh install gives eric uid 1000; nothing here depends on the uid)
   - **Wi-Fi country / unblock** (else wlan is dead on first boot — bit us twice
     2026-07-12; there are THREE persistence layers and all must agree):
     1. regdom: append ` cfg80211.ieee80211_regdom=US` to `cmdline.txt` (keep it one line!);
     2. rfkill: on the rootfs write `0` into `/var/lib/systemd/rfkill/*:wlan` (image
        ships them as `1` = blocked; systemd-rfkill restores the state every boot);
     3. NetworkManager's own radio switch: if the system ever boots while blocked, NM
        writes `WirelessEnabled=false` to `/var/lib/NetworkManager/NetworkManager.state`
        and re-blocks the radios itself on every later boot even after 1+2 are fixed.
        Fix live with `sudo nmcli radio wifi on` (persists), or pre-seed the file on the
        rootfs with `[main]` + `WirelessEnabled=true`.
   - (optional, smooths headless bring-up) on the rootfs: pre-copy `Holonet.nmconnection`
     into `/etc/NetworkManager/system-connections/` (0600 root:root) so it joins the home
     LAN immediately; pre-set `/etc/hostname` + `/etc/hosts` (kingfisher) and
     `/etc/localtime → /usr/share/zoneinfo/Etc/UTC` — step 4 is then already done.
3. Partition plan — 128G root, rest `/home` (~804G on a 1TB SSD; the roomy root leaves
   space for e.g. /var/lib/docker). **Gotcha: the firstboot auto-resize REFUSES to grow
   root unless it is the last partition** — pre-creating p3 as a "growth boundary" silently
   disables the resize, leaving the 2.3G image-sized root. So lay it out yourself from any
   Linux machine with the SSD attached (shown as `/dev/nvme0n1`; before first boot or after,
   both work):
   ```sh
   sudo parted /dev/nvme0n1 -- mkpart primary ext4 128GiB 100%    # creates p3 (/home)
   sudo mkfs.ext4 -L home /dev/nvme0n1p3
   sudo e2fsck -f /dev/nvme0n1p2
   sudo parted /dev/nvme0n1 unit s print                          # note p3's start sector
   sudo parted /dev/nvme0n1 -- resizepart 2 268435455s            # = p3 start − 1
   sudo resize2fs /dev/nvme0n1p2
   ```
   Then add `/home` to fstab (mount p2 to reach its `/etc`):
   ```sh
   echo 'PARTUUID=xxxxxxxx-03  /home  ext4  defaults,noatime  0  2' >> <p2>/etc/fstab
   ```
   (real PARTUUID from `blkid`; it's the disk id + `-03`). `/home` starts empty; the user
   dir is created on first boot, and flight data is restored later (Phase 12) if you have a
   backup.
4. First boot basics:
   ```sh
   sudo hostnamectl hostname kingfisher
   sudo timedatectl set-timezone Etc/UTC
   sudo timedatectl set-local-rtc 0
   ```

## Phase 2 — base system

```sh
# etckeeper FIRST, so every subsequent config change is auto-tracked in git:
sudo apt update && sudo apt install -y etckeeper

# hardware watchdog: 15s (vendor file 40-rpi-enable-watchdog.conf already sets 1min;
# the 99- prefix is required to override it):
sudo mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15\n' | sudo tee /etc/systemd/system.conf.d/99-watchdog.conf

# passwordless sudo — via visudo:
#   eric ALL=(ALL) NOPASSWD: ALL
sudo EDITOR=vim visudo

sudo usermod -aG adm,dialout,plugdev eric      # plugdev is load-bearing (IIO access)

# free ttyAMA10 debug UART from the login getty (GPS uses ttyAMA0, but this was done deliberately)
sudo systemctl disable --now serial-getty@ttyAMA10.service

# ghostty terminfo (run FROM your workstation):
#   infocmp -x | ssh eric@kingfisher 'tic -x -'

# EEPROM (verify with: sudo rpi-eeprom-config)
#   BOOT_UART=1, BOOT_ORDER=0xf461 (SD first, then NVMe, then USB — SD-first means an
#   inserted card wins over the SSD; that's desirable while the SD is the fallback),
#   NET_INSTALL_AT_POWER_ON=1, FIRMWARE_RELEASE_STATUS="latest" in /etc/default/rpi-eeprom-update
```

## Phase 3 — apt packages

Third-party repos first:

```sh
# Caddy
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
# GitHub CLI
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/etc/apt/keyrings/githubcli-archive-keyring.gpg && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list
sudo apt update
```

Core install (everything the flight stack + your workflow actually uses):

```sh
sudo apt install -y \
  build-essential cmake pkg-config git gh vim tmux tree htop ncdu jq sqlite3 \
  gdb strace tcpdump stress rsync zip unzip p7zip-full \
  i2c-tools python3-smbus2 python3-pip python3-venv python-is-python3 \
  gpsd gpsd-clients pps-tools chrony \
  caddy libpam0g-dev \
  linux-headers-rpi-2712 \
  avahi-daemon network-manager
# SDR / airband stack (only if keeping airband — see Improvements):
sudo apt install -y rtl-sdr librtlsdr-dev libshout3-dev libmp3lame-dev libfftw3-dev libconfig++-dev libpulse-dev libsoapysdr-dev
```

**[DISCUSS — cruft, deliberately omitted]**: `cursor` (side-loaded .deb IDE + its
`50-cursor.conf` sysctl), `mkvtoolnix`, `ghostscript`, `poppler-utils`, `qpdf`,
`python3-pymupdf`, `python3-pypdf`, `cifs-utils`, `ntfs-3g`, `lua5.1`, `luajit`,
`rpi-connect-lite` (installed but off), `rpi-usb-gadget`, `usb-modeswitch`,
`rpicam-apps-lite`, `kms++-utils`, `v4l-utils`, `mkvtoolnix`, `firmware-atheros/libertas/mediatek/realtek`
(unless the wlan1 dongle needs one — check `lsusb`/`dmesg` for which firmware wlan1 loads!).

## Phase 4 — toolchains (non-apt)

```sh
# Go (tarball, NOT apt) — check go.dev/dl for current version:
wget https://go.dev/dl/go1.26.4.linux-arm64.tar.gz
sudo rm -rf /usr/local/go && sudo tar -C /usr/local -xzf go1.26.4.linux-arm64.tar.gz

# Rust + espflash (ESP32-C3 pod firmware flashing):
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
cargo install espflash

# uv (python):
curl -LsSf https://astral.sh/uv/install.sh | sh

# atuin (shell history; installer adds the .bashrc hooks itself):
curl --proto '=https' --tlsv1.2 -LsSf https://setup.atuin.sh | sh
atuin import auto     # then restore ~/.local/share/atuin/ from a backup to keep history

# Claude Code:
curl -fsSL https://claude.ai/install.sh | bash    # (or current official method)

# ~/.bashrc additions (drop the stratux path — cruft):
export GOPATH=$HOME/go
export PATH=$HOME/.local/bin:$HOME/go/bin:${PATH}:/usr/local/go/bin
[ -e "$HOME/.ssh/agent.sock" ] && export SSH_AUTH_SOCK="$HOME/.ssh/agent.sock"
```

## Phase 5 — boot configuration

Append to `/boot/firmware/config.txt` (under `[all]`) — this is the complete delta vs stock:

```ini
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=100000

# --- Flight sensor stack ---
#dtoverlay=icm20948      # NOT used: 0x68 address conflict with icm45686 (both 0x68)
dtoverlay=icm45686,int_trigger=8
#dtoverlay=mmc5983ma                     # disabled: mag lives on the ESP32 pod
#dtoverlay=i2c-sensor,bmp280,addr=0x77   # disabled: sensor unplugged
dtoverlay=uart0-pi5
dtoverlay=pps-gpio,gpiopin=18
dtparam=rtc_bbat_vchg=3000000

# --- NVMe ---
dtparam=pciex1_gen=3   # Gen 3 (~900 MB/s vs ~450 default); not RPi-certified but verified
                       # error-free on this HAT + Kingston NV3 (2026-07-12). If dmesg ever
                       # shows AER corrected-error spam or nvme timeouts, remove this line.
```

Gotchas encoded there: `int_trigger=8` is **mandatory** (INT1 wired active-low; default
edge-rising probes but never interrupts); 3.0 V RTC charge is the correct value for the
official cell — do not raise; the two `dtoverlay` lines for icm45686/mmc5983ma are added
automatically by `make install` in Phase 6, so add the rest and let make handle those
(or add all and make will skip).

`/boot/firmware/cmdline.txt`: append `cfg80211.ieee80211_regdom=US` to the single line.

## Phase 6 — out-of-tree kernel modules + overlays (DKMS)

The IMU and magnetometer drivers are custom builds, managed by **DKMS** (adopted
2026-07-12 — each repo now carries a `dkms.conf`; commit them!). DKMS auto-rebuilds
both modules on every kernel upgrade. What motivated this: an mmc5983ma module built by
hand was found dead after a 6.18.33→.34 kernel bump — in the plane that's a dead sensor.

```sh
sudo apt install -y dkms    # (if not already via Phase 3)
mkdir -p ~/go/src/github.com/westphae && cd $_
git clone https://github.com/westphae/icm45686-mod    # vendored mainline inv_icm45600 driver
git clone https://github.com/westphae/mmc5983ma-mod

# modules via dkms:
sudo ln -sfn $PWD/icm45686-mod  /usr/src/icm45686-1.0
sudo ln -sfn $PWD/mmc5983ma-mod /usr/src/mmc5983ma-1.0
sudo dkms add icm45686/1.0 && sudo dkms build icm45686/1.0 && sudo dkms install icm45686/1.0
sudo dkms add mmc5983ma/1.0 && sudo dkms build mmc5983ma/1.0 && sudo dkms install mmc5983ma/1.0

# overlays + config.txt lines (module part now handled by dkms — do NOT run plain `make install`):
(cd icm45686-mod  && make dtbo && sudo make dtbo_install config_enable)
(cd mmc5983ma-mod && make dtbo && sudo make dtbo_install)   # dtbo staged but overlay stays
#   commented in config.txt — the mag is on the ESP32 pod, not the Pi bus (decided 2026-07-12)
sudo reboot
```

Verify after reboot: `dkms status` shows both installed; `ls /sys/bus/iio/devices/`
shows the IMU devices; `grep 17 /proc/interrupts` counts up when the IMU samples.
Note: if the driver source changes, rebuild with
`sudo dkms remove <name>/1.0 --all && sudo dkms add/build/install <name>/1.0`.

## Phase 7 — non-root IIO access chain (three files + group)

All three ship in the mod repos' `etc/` dirs; contents for reference:

`/etc/udev/rules.d/99-iio.rules`:
```
SUBSYSTEM=="iio", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="iio", KERNEL=="iio:device*", \
    RUN+="/bin/sh -c 'chgrp -R plugdev /sys%p && chmod -R g+rwX /sys%p'"
SUBSYSTEM=="iio", KERNEL=="trigger[0-9]*", \
    RUN+="/bin/sh -c 'chgrp -R plugdev /sys%p && chmod -R g+rwX /sys%p'"
```

`/etc/tmpfiles.d/iio.conf`:
```
z /sys/kernel/config/iio/triggers/hrtimer 0775 root plugdev - -
```

`/etc/modules-load.d/iio-trig-hrtimer.conf`:
```
iio-trig-hrtimer
```

## Phase 8 — time sync (GPS + PPS → chrony)

1. `/etc/default/gpsd`:
   ```
   START_DAEMON="true"
   USBAUTO="false"
   DEVICES="/dev/ttyAMA0 /dev/pps0"
   GPSD_OPTIONS="-n -b -s 115200"
   GPSD_SOCKET="/var/run/gpsd.sock"
   ```
   (`-b` read-only is required to preserve the receiver's lean 10 Hz NAV-PVT stream;
   `-s 115200` because `-b` disables autobaud. `/dev/ttyAMA0` NOT `/dev/serial0`.)

2. `/etc/chrony/chrony.conf` — three edits to the stock file:
   - change `makestep 1 3` → `makestep 0.5 3` (never step mid-flight)
   - **comment out the default `pool 2.debian.pool.ntp.org iburst`** (GPS-only time — see Improvements #6)
   - add:
   ```
   refclock SOCK /run/chrony.clk.ttyAMA0.sock refid GPS precision 1e-1 offset 0.0 delay 0.1 poll 2 filter 3 noselect
   refclock SOCK /run/chrony.pps0.sock refid PPS precision 1e-7 lock GPS poll 2
   ```
   (GPS is `noselect` on purpose — gpsd's NMEA SOCK latency wanders per-restart; PPS is
   the sole timekeeper, locked to GPS for the second numbering. Do not "fix" the offset.)

3. `/etc/chrony/conf.d/logging.conf`: `log tracking measurements statistics`

4. `/etc/udev/rules.d/99-pps-chrony.rules`:
   ```
   KERNEL=="pps*", GROUP="_chrony", MODE="0660"
   ```

5. Helper scripts + unit (all in kingfisher repo `deploy/time-sync/`):
   ```sh
   sudo install -m 755 deploy/time-sync/kingfisher-resync-time.sh /usr/local/bin/
   sudo install -m 755 deploy/time-sync/kingfisher-clock-check.sh /usr/local/bin/
   sudo install -m 644 deploy/time-sync/kingfisher-clock-check.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable kingfisher-clock-check.service
   ```

6. `/etc/sudoers.d/kingfisher-resync` (mode 0440, validate with `visudo -cf`):
   ```
   eric ALL=(root) NOPASSWD: /usr/local/bin/kingfisher-resync-time.sh, /usr/bin/chronyc
   ```

7. Codify the restart ordering (chronyd creates the SOCK refclock sockets; gpsd connects
   to them) — `/etc/systemd/system/gpsd.service.d/chrony-order.conf`:
   ```ini
   [Unit]
   After=chrony.service
   PartOf=chrony.service
   ```
   Verified 2026-07-12: `systemctl restart chrony` now restarts gpsd automatically and
   both refclocks resume samples. (`kingfisher-resync-time.sh` also still does it explicitly.)

## Phase 9 — journald (persistent, SSD-sized, spam-guarded)

`/etc/systemd/journald.conf.d/99-persistent-capped.conf` (the `99-` prefix must sort
after the vendor `40-rpi-volatile-storage.conf` that forces volatile):

```ini
[Journal]
Storage=persistent
SystemMaxUse=2G
SystemMaxFileSize=128M
MaxRetentionSec=6month
SyncIntervalSec=30s
RateLimitIntervalSec=30s
RateLimitBurst=5000
```

## Phase 10 — networking

```sh
# Access point (open network — see Improvements #1):
sudo nmcli con add type wifi ifname wlan0 con-name kingfisher ssid kingfisher \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
  802-11-wireless.powersave 2 ipv4.method shared ipv4.addresses 192.168.10.1/24 \
  connection.autoconnect yes connection.autoconnect-priority 100

# Client networks: copy the saved .nmconnection files into
# /etc/NetworkManager/system-connections/ (root:root, mode 0600), then
# nmcli con reload    — or re-add with nmcli device wifi connect.
```

`/etc/NetworkManager/dnsmasq-shared.d/kingfisher-efb.conf` (EFB DHCP reservations):
```
dhcp-host=5c:33:7b:ec:ab:2d,192.168.10.158,Pixel-8-Pro
dhcp-host=f8:b1:dd:97:60:81,192.168.10.230,iPad-Mini
```

`/etc/avahi/avahi-daemon.conf` deltas from stock: `enable-wide-area=yes`,
`publish-workstation=yes`, `ratelimit-interval-usec=1000000`, `ratelimit-burst=1000`.

## Phase 11 — Caddy

`/etc/caddy/Caddyfile` (canonical copy + full README in repo `deploy/caddy/`):
```
https://kingfisher.lan, https://kingfisher, https://192.168.10.1, https://192.168.86.151, https://127.0.0.1 {
	tls internal
	# kingfisher binds 127.0.0.1:8080 and enforces the AP trusted-device allowlist
	# itself; Caddy terminates TLS and passes the unspoofable real client IP.
	reverse_proxy 127.0.0.1:8080 {
		header_up X-Real-Client-IP {remote_host}
	}
}
http://kingfisher.lan, http://kingfisher, http://192.168.10.1, http://192.168.86.151, http://127.0.0.1 {
	redir https://{host}{uri} permanent
}
```
`sudo systemctl reload caddy`. The internal CA regenerates on a fresh build → re-trust the
new root cert on the tablets (see `deploy/caddy/README.md`; `sudo caddy trust` locally for
curl over https). `192.168.86.151` is the Pi's wlan1 address — DHCP-reserve it on the home
router so it stays put; mDNS (`kingfisher.lan`) also works and doesn't depend on the lease.

## Phase 12 — kingfisher app

```sh
cd ~/go/src/github.com/westphae
git clone https://github.com/westphae/kingfisher
# go.mod replace directives need the siblings — REQUIRED for the build:
git clone https://github.com/westphae/go-iio
git clone https://github.com/westphae/goflying
git clone https://github.com/westphae/geomag
git clone https://github.com/westphae/magkal

cd kingfisher && go install ./cmd/kingfisher
sudo install -m 755 deploy/poweroff/kingfisher-poweroff.sh /usr/local/bin/   # (path per repo)

# sudoers drop-in /etc/sudoers.d/kingfisher-poweroff (0440):
#   eric ALL=(root) NOPASSWD: /usr/local/bin/kingfisher-poweroff.sh

# user unit (template in repo, sed __KINGFISHER_BIN__ → ~/go/bin/kingfisher):
mkdir -p ~/.config/systemd/user
sed "s|__KINGFISHER_BIN__|$HOME/go/bin/kingfisher|g" \
  deploy/systemd/kingfisher.service.example > ~/.config/systemd/user/kingfisher.service

# log-spam guard drop-in ~/.config/systemd/user/kingfisher.service.d/log-rate-limit.conf:
#   [Service]
#   LogRateLimitIntervalSec=30s
#   LogRateLimitBurst=300

sudo loginctl enable-linger eric        # user service starts at boot — silent failure if missed
systemctl --user daemon-reload && systemctl --user enable --now kingfisher.service

# restore flight data + config from backup (optional; the app runs with empty dirs):
#   rsync -a <backup>/kingfisher/ ~/kingfisher/
#   rsync -a <backup>/.config/kingfisher/ ~/.config/kingfisher/
# A restored config.json must keep "http_addr": "127.0.0.1:8080" — the app binds
# loopback so Caddy is the sole ingress; a public bind lets AP clients skip the
# trusted-device allowlist by hitting :8080 directly.
```

## Phase 13 — airband receiver **[KEEP — decided 2026-07-12, active project]**

```sh
git clone https://github.com/rtl-airband/RTLSDR-Airband.git   # v5.2.0 as of 2026-07-12; cmake build
cd RTLSDR-Airband && mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release .. && make -j$(nproc) && sudo make install   # → /usr/local/bin/rtl_airband

# /etc/udev/rules.d/99-rtl-sdr.rules:
#   SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", SYMLINK+="rtl_sdr", MODE="0664", GROUP="plugdev"
#   SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", SYMLINK+="rtl_sdr", MODE="0664", GROUP="plugdev"
# /etc/modprobe.d/no-rtl.conf:
#   blacklist dvb_usb_rtl28xxu
```
Note: rtl_airband has historically been **run ad hoc, with no systemd unit enabled**
(repo `westphae/kingfisher-airband` has `deploy/airband.service`). Decide whether to
service-ify it or keep running it by hand.

## Phase 14 — verification checklist

```sh
vcgencmd get_throttled                    # 0x0 on bench power
cat /sys/bus/pci/devices/0001:01:00.0/current_link_speed   # 8.0 GT/s (PCIe gen3)
sudo dmesg | grep -iE 'aer|nvme.*timeout'  # no corrected-error spam at gen3
chronyc tracking                          # Reference ID ...(PPS), within ~15-20s of GPS fix
chronyc sources -v                        # GPS noselect + PPS locked
ss -xp | grep chrony                      # gpsd connected to both SOCKs
ls /sys/bus/iio/devices/                  # icm45686 + mmc5983ma present
systemctl --user status kingfisher        # active; journalctl --user-unit=kingfisher
curl -k https://192.168.10.1/             # caddy → app (from an AP client)
iw dev wlan0 get power_save               # off
journalctl -b -u 'systemd-fsck*'          # clean
sudo dmesg | grep "setting system clock"  # RTC held time across a cold boot
```

---

# CRUFT — deliberately not installed

These were found as junk on an earlier build and are intentionally left out; don't add
them back without a reason.

1. **Default `pi` user** — a fresh install with `userconf.txt` naming eric avoids the
   stock `pi` account entirely.
2. **Cursor IDE** — dev tool, not part of the unit. **Decision: omit**
   (reinstall ad hoc if wanted; its `/etc/sysctl.d/50-cursor.conf` goes with it).
3. **PDF/media tooling** (ghostscript, poppler-utils, qpdf, pymupdf/pypdf, pdfplumber,
   mkvtoolnix) — was for one-off Cursor-agent data analysis. **Decision: omit** (use `uvx`).
4. **`/opt/stratux/bin` in PATH** — **Decision: omit** (already dropped from the Phase 4 PATH line).
5. **Backup-file litter** (`chrony.conf.bak.*` ×6 etc.) — **Decision: omit**; etckeeper
   (now installed) replaces the habit.
6. **`rpi-connect-lite` / `mpris-proxy`** — don't install; rpi-connect is a relay/tunnel
   service, forbidden by the security posture (Appendix A).
7. **`rfkill_default.conf`** — **Decision: omit.**
8. **`rpi-usb-gadget`, `usb-modeswitch`, `cifs-utils`, `ntfs-3g`, `lua5.1/luajit`,
   camera/video stacks** (`rpicam-apps-lite`, `v4l-utils`, `kms++-utils`) — no evidence of use.
9. **bmp581-mod / icm42688-mod / icm20948-mod build trees** — experiments superseded by
   icm45686+mmc5983ma. They live only in ~/go/src (no system footprint); keep the repos,
   just don't install them.
10. **Wi-Fi firmware for other vendors** (atheros/libertas/mediatek/realtek) — keep only
    what wlan1's chipset actually needs (check before omitting).

# IMPROVEMENTS — forward-looking notes. The items already folded into the phases above
# (DKMS, watchdog, chrony→gpsd ordering, etckeeper) are marked inline. Power root-cause
# of the 2026-07-11 failure: an aging battery pack sagging below 40% charge — a proper
# aircraft supply is still needed (#4).

1. **Secure the AP.** The `kingfisher` network is **open** (no WPA). Anyone at the tiedown
   can join, get DHCP, reach the app and the GDL90 stream. Add WPA2-PSK
   (`wifi-sec.key-mgmt wpa-psk`); EFB apps handle it fine. Biggest single win.
2. **DKMS for the two kernel modules.** Today a routine `apt full-upgrade` that bumps the
   kernel silently breaks the IMU/magnetometer until you rebuild by hand — in the plane
   that's a dead sensor stack. Packaging icm45686-mod and mmc5983ma-mod as dkms modules
   makes rebuilds automatic. (Interim: `apt-mark hold linux-image-rpi-2712` before flights.)
3. **Hardware watchdog.** Pi 5 has one; set `RuntimeWatchdogSec=15` in
   `/etc/systemd/system.conf.d/watchdog.conf` so a hung system reboots itself in flight
   instead of needing a power pull.
4. **Power** (root cause of the 2026-07-11 failures): fit a proper 5 V/5 A supply from the
   aircraft bus. Undervoltage is now surfaced — kingfisher's `system` telemetry device polls
   the 5 V rail + `get_throttled` and shows a cockpit warning. Longer term: the supercap/UPS
   clean-shutdown path (the poweroff API and script already exist — a GPIO shutdown button
   via `dtoverlay=gpio-shutdown` is a cheap interim step).
5. **Codify the chrony→gpsd restart ordering** instead of remembering it: drop-in
   `/etc/systemd/system/gpsd.service.d/chrony-order.conf` with `PartOf=chrony.service`
   (+ existing `After=`), so restarting chrony automatically restarts gpsd.
6. **NTP fallback when GPS is absent.** The Debian pool is commented out, so indoors the
   clock free-runs from RTC. Consider re-adding the pool (it's ignored while PPS is
   selected, and wlan1 provides it a path when present) — or leave GPS-only if you prefer
   determinism; discuss.
7. **sshd hardening**: with keys in place, `PasswordAuthentication no` (the AP is open —
   see #1 — so ssh is exposed to anyone who joins).
8. **Put /etc under version control** (`sudo apt install etckeeper`, git backend). Every
   future tweak is then self-documenting — this playbook stops going stale, and the
   `.bak.*` litter habit dies.
9. **Automatic flight-data backup**: a timer that rsyncs `~/kingfisher/flights` to your
   workstation/cloud whenever wlan1 is online; 3.1 G of irreplaceable data currently
   lives on one device in an aircraft.
10. **Disable `apt-daily*` timers** (or set them `Persistent=false` + a maintenance window):
    background apt during preflight/flight adds I/O and, with #2 unsolved, could even
    swap a kernel out from under the sensors.
11. **Caddyfile hostnames**: replace the hardcoded home-LAN IP `192.168.86.151` with the
    mDNS name only, or add a wildcard — the IP changes with DHCP.
12. **fstrim** is already on (`fstrim.timer`) and fstab uses `noatime` — good for the SSD;
    nothing to do, just noting it's covered.

---

# Appendix A — Security posture (decided 2026-07-12)

**Threat model (Eric's, deliberate):** proximity threats (airport ramp, hangar
neighbors, formation traffic) are ACCEPTED — the AP stays **open**, ssh keeps
password auth, no firewall/fail2ban/WPA2. The concern that matters is internet
exposure while developing from home. Prefer lighter security; don't re-litigate this.

**The one rule that is the internet security:**

> **Never port-forward to this machine, and never run a relay/tunnel service on it**
> (rpi-connect was purged for this reason). Behind home NAT the Pi is unreachable
> from the internet; on the AP, only radio-range devices exist. That's the perimeter.

**Minimal viable set (all already in place):**
1. No port forwarding / no tunnel services — see above.
2. One decent password on `eric` — it gates ssh and the web terminal, and with
   `NOPASSWD: ALL` sudo it is effectively root. Protect it accordingly.
3. Caddy TLS (`tls internal`) — keeps that password unsniffable on the open AP.
   Zero ongoing cost; keep it.
4. journald persistence + etckeeper — not security, but the audit trail.

**Explicitly skipped (don't add back without a threat-model change):** WPA2 on the
AP, key-only sshd, fail2ban, nftables/ufw, port knocking.

**Web terminal + power API — gated to trusted AP devices** *(kingfisher-specific)*:
`/terminal` is a root-equivalent shell and `POST /api/power/off` is unauthenticated; on
the open AP both are one URL away for anyone in radio range. kingfisher enforces an AP
trusted-device allowlist itself (`internal/web/access.go`):

- it binds `127.0.0.1:8080`, so Caddy is the **sole ingress** — a public bind would let
  AP clients skip the gate by hitting `:8080` directly;
- it returns `403` on `/terminal*`, `/api/terminal/*`, `/api/power/*`, and `/api/access*`
  for AP-subnet clients whose IP is not in `config.access.trusted_ips`; everything off the
  AP (loopback, home LAN, Tailscale) is always allowed;
- Caddy passes the real client IP via `header_up X-Real-Client-IP {remote_host}` (Phase 11),
  which overwrites any client-sent value and so cannot be spoofed.

Manage the list from the cockpit UI (**More → Trusted devices** — lists AP clients, lets a
trusted device grant/revoke and name each); the EFBs are seeded trusted. Known limitation
(accepted): same-L2 IP spoofing defeats an IP allowlist — outside the threat model. Full
notes in `deploy/caddy/README.md`.

**Remote access from outside home (optional, when wanted):** do NOT port-forward —
install **Tailscale** on the Pi + laptop (zero-config WireGuard mesh; Pi reachable
from your devices anywhere, no open ports, nothing else gains access). The secure
option is also the convenient one here.
