# GNSS Time Sync

Kingfisher records everything against the host's `CLOCK_REALTIME` time base. The
flight DB filename, `_session.start_time`, buffered IIO timestamps on
`current_timestamp_clock == realtime`, pod wall-clock reconstruction, and
derived-stream timestamps all assume the Pi wall clock is already sane.

Per-table **`ts_ns`** semantics, GPS **`fix_time_unix_s`** vs row time, and
sensor-fusion guidance: **`docs/timestamps.md`**.

The intended deployment model is:

1. `gpsd` reads the M9N on `/dev/ttyAMA0` in read-only mode.
2. `chronyd` disciplines the Pi clock from gpsd over Unix-domain SOCK refclocks.
3. With PPS wired, gpsd also feeds a PPS SOCK; chrony uses PPS for sub-second
   discipline and the UART SOCK for time-of-day (`lock GPS`).
4. Kingfisher starts after chrony is healthy and reports live Pi-vs-GPS clock
   health in the cockpit header.

Repo-owned starting points live in `deploy/time-sync/`:

- `deploy/time-sync/gpsd.default.example` → `/etc/default/gpsd`
- `deploy/time-sync/chrony.conf.example` → `/etc/chrony/chrony.conf`
- `deploy/time-sync/99-pps-chrony.rules` → only if using kernel `refclock PPS`
  (not recommended here; see below)
- `deploy/time-sync/verify.md` — post-install checklist

## Hardware

| Signal | Pi 5 header | Device |
|--------|-------------|--------|
| M9N UART TX/RX | GPIO 14/15 | `/dev/ttyAMA0` |
| M9N PPS out | GPIO 18 (physical pin 12) | `/dev/pps0` via `pps-gpio` overlay |

PPS is optional for bring-up but recommended for flight recording accuracy.

## 1. Install packages

```bash
sudo apt install gpsd gpsd-clients chrony
sudo apt install pps-tools    # optional; provides ppstest
```

## 2. Configure gpsd

Copy `deploy/time-sync/gpsd.default.example` into **`/etc/default/gpsd`**
(or merge by hand):

```ini
START_DAEMON="true"
USBAUTO="false"

# UART + PPS (omit /dev/pps0 for UART-only bring-up)
DEVICES="/dev/ttyAMA0 /dev/pps0"

GPSD_OPTIONS="-n -b -s 115200"
GPSD_SOCKET="/var/run/gpsd.sock"
```

- **`-n`** — poll the receiver immediately (required for chrony without a UI client).
- **`-b`** — read-only; preserves the lean u-blox binary stream kingfisher expects.
- **`-s 115200`** — required when the M9N UART baud was saved at 115200 (see README
  GPS section). With `-b`, gpsd does not autobaud; without `-s` it stays at 19200
  and fixes never arrive.

Enable **`gpsd.service`** at boot. Debian also installs `gpsd.socket`, which
starts gpsd only when something connects to `/run/gpsd.sock` (e.g. `cgps`).
Chrony refclocks need gpsd running continuously:

```bash
sudo systemctl enable --now gpsd.service
```

The shipped unit already has `After=chronyd.service`.

## 3. PPS device tree (PPS setups only)

Add to **`/boot/firmware/config.txt`** (Bookworm) or **`/boot/config.txt`**
(older images), then **reboot**:

```txt
dtoverlay=pps-gpio,gpiopin=18
```

After reboot:

```bash
ls -l /dev/pps0
sudo ppstest /dev/pps0    # one assert line per second; Ctrl-C to stop
```

Do not add PPS refclock lines to chrony until `/dev/pps0` exists.

## 4. Configure chrony

On Debian and Raspberry Pi OS, chrony config lives at
**`/etc/chrony/chrony.conf`** (some distros use `/etc/chrony.conf`). Merge
`deploy/time-sync/chrony.conf.example` or use the block below.

Prefer gpsd **SOCK** refclocks over SHM. Recent gpsd creates:

- `/run/chrony.clk.ttyAMA0.sock` — serial time-of-day from the UART
- `/run/chrony.pps0.sock` — PPS timestamps (when `/dev/pps0` is in gpsd `DEVICES`)

### UART-only (no PPS)

```conf
makestep 1.0 3
rtcsync

# Offline-first flight recording: leave pool commented out.
#pool pool.ntp.org iburst

refclock SOCK /run/chrony.clk.ttyAMA0.sock refid GPS precision 1e-1 offset 0.62 delay 0.1
```

### UART + PPS (recommended)

```conf
makestep 1.0 3
rtcsync

#pool pool.ntp.org iburst

refclock SOCK /run/chrony.clk.ttyAMA0.sock refid GPS precision 1e-1 offset 0.62 delay 0.1
refclock SOCK /run/chrony.pps0.sock refid PPS precision 1e-7 lock GPS
```

Directive notes:

- **`makestep 1.0 3`** — allow a few large steps early at boot, then slew only.
- **`offset`** — added to the gpsd serial SOCK timestamp (see tuning section).
- **`lock GPS`** — PPS steers the clock but needs the GPS refclock for which
  UTC second each pulse belongs to.
- **`pool`** — fine on the bench with internet; for offline recording, comment
  it out so chrony selects GPS/PPS instead of NTP.

Use **SOCK PPS** (above). Do **not** also add `refclock PPS /dev/pps0`: gpsd
opens `/dev/pps0` when a GPS is present, and chrony cannot share the device.
Kernel `refclock PPS` would need `deploy/time-sync/99-pps-chrony.rules` and a
gpsd-free PPS path — not worth it on this deployment.

On systems that use `/var/run` instead of `/run`, adjust SOCK paths to match
what chronyd creates.

## 5. Tune the GPS `offset`

Chrony **adds** the `offset` value (seconds) to each gpsd serial-time sample.
The correct value is **not** the `cgps` “time offset” display (see below).

### Procedure (NTP cross-check)

Use NTP as a trusted reference, measure the raw GPS SOCK error at `offset 0.0`,
then apply the same sign in `chrony.conf`:

1. In `/etc/chrony/chrony.conf`: set **`offset 0.0`**, enable **`pool
   pool.ntp.org iburst`**, comment out the **PPS** refclock line.
2. Restart in order:

   ```bash
   sudo systemctl restart chronyd
   sleep 2
   sudo systemctl restart gpsd
   ```

3. Wait for NTP and GPS samples (30–60 s after gpsd restart):

   ```bash
   chronyc waitsync 30 0.1
   sleep 45
   chronyc sourcestats | grep GPS
   ```

4. Read the GPS **`Offset`** column (e.g. `+620ms`). Set:

   ```conf
   offset 0.62
   ```

   **Same sign, value in seconds** — do **not** negate. Generic gpsd/chrony
   examples often say to flip the sign; on this M9N + SOCK path that is wrong
   (`offset -0.62` with a `+620ms` reading yields ~`+1.3 s` residual;
   `offset +0.62` lands near zero).

5. Restore offline config: comment **`pool`**, re-enable the **PPS** line if used,
   restart **`chronyd`** then **`gpsd`**, verify:

   ```bash
   chronyc sourcestats | grep -E 'GPS|PPS'
   chronyc sources -v | grep -E 'GPS|PPS'
   ```

   Target: GPS **`Offset`** within a few tens of ms; with PPS, expect **`#* PPS`**
   (disciplining) and **`#- GPS`** (time-of-day via `lock GPS`).

Re-tune if you change receiver baud, gpsd version, or move from UART-only to PPS.

### Do not use `cgps` for this

| Display | What it compares | Typical value here |
|---------|------------------|-------------------|
| **`cgps` time offset** | TPV fix epoch vs host clock | ~600–700 ms |
| **`chronyc sourcestats` GPS** | gpsd serial SOCK vs host/NTP | ~+620 ms at `offset 0.0` |
| **`gpsmon` `PPS offset:`** | PPS pulse vs host clock | sub-ms when PPS is healthy |

These are three different pipelines. Tune chrony from **`sourcestats`**, not
from `cgps`.

## 6. Service restarts

**Always restart in this order** after editing `chrony.conf`:

```bash
sudo systemctl restart chronyd
sleep 2
sudo systemctl restart gpsd
```

If you restart `chronyd` alone, gpsd keeps stale socket connections and logs
`chrony_send(...) Transport endpoint is not connected` — GPS/PPS show **`#?`**
with **Reach 0** until gpsd is restarted.

Cold boot order is handled by systemd (`chronyd` before `gpsd`). After manual
edits, you must restart both.

## 7. Verification

```bash
systemctl is-active gpsd chronyd    # both active
cgps -s                             # 3D fix on /dev/ttyAMA0
gpsmon                              # PPS offset: near zero (PPS setups)
chronyc tracking
chronyc sources -v
chronyc sourcestats
```

Healthy PPS deployment (pool commented out, offset tuned):

- GPS and PPS **Reach** non-zero in `chronyc sources -v`
- **`#* PPS`** — chrony disciplining from PPS
- **`#- GPS`** — serial source providing time-of-day (`lock GPS`)
- GPS **`sourcestats` Offset** near zero; PPS offset in µs

See `deploy/time-sync/verify.md` for the kingfisher UI checks.

## 8. Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| GPS/PPS `#?`, Reach `0`, `sourcestats` NP `0` | `gpsd.service` not running — `systemctl enable --now gpsd.service` |
| Reach `0` after editing chrony | Restart **`gpsd`** after **`chronyd`** |
| gpsd log: `Transport endpoint is not connected` | Same — stale SOCK after chrony restart |
| GPS `#?` / `#x`, large `sourcestats` Offset | Wrong **`offset` sign** (negated) or magnitude — re-run NTP tune |
| NTP `*` instead of PPS `*` | **`pool`** enabled while online — comment out for offline |
| PPS Reach `0` | `/dev/pps0` missing (no overlay/reboot), or not in gpsd **`DEVICES`** |
| PPS samples OK but `#x`, GPS `#x` | GPS **`offset`** still wrong — PPS `lock GPS` needs serial within ~0.5 s |
| `cgps` OK, chrony dead | Almost always gpsd not feeding SOCK — restart pair above |

## 9. Expected accuracy

**UART-only:** seconds-level discipline, sub-second alignment realistic, not
sub-millisecond.

**UART + PPS:** sub-millisecond clock discipline once `#* PPS`; UART still
carries date/time.

### Kingfisher clock badge

The cockpit header shows **Pi time** discipline from chrony when available:

- **PPS synced** + **Correction** — chrony is steering from PPS; the correction
  is chrony's last offset (typically nanoseconds).
- **GPS synced** — UART serial time only (seconds-level accuracy).
- **PPS wired, idle** — `/dev/pps0` exists but chrony is not selecting PPS
  (check `refclock SOCK /run/chrony.pps0.sock` in `chrony.conf`).
- **GPS data** — age of the last gpsd fix epoch used for cross-check.
- **Est. error** — kingfisher-only fallback when chrony is unavailable; compares
  host wall time to the fix epoch after subtracting typical receiver lag.

Hover the badge for a full tooltip. This is complementary to `chronyc tracking`:
the header answers "what steers the Pi clock and by how much?" while the GPS
cross-check catches gpsd outages or gross wall-clock drift.

For what each flight DB **`ts_ns`** column means (including GPS row time vs
**`fix_time_unix_s`**), see **`docs/timestamps.md`**.

## 10. Kingfisher startup ordering

Kingfisher performs a short startup assessment of Pi-vs-GPS time before opening
the flight DB (`/api/status` and cockpit header). To protect DB filenames and
session start times on cold boot, order kingfisher after chrony is healthy:

```ini
[Unit]
After=chronyd.service gpsd.service
Wants=chronyd.service gpsd.service

[Service]
ExecStartPre=/usr/bin/chronyc waitsync 20 0.25
```

That waits for sync at startup; kingfisher's status model still reports if the
clock later drifts or goes stale.
