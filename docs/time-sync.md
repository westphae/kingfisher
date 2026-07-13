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
3. With PPS wired, gpsd also feeds a PPS SOCK; chrony disciplines the clock from
   PPS (sub-µs) and uses the UART SOCK only to number each PPS second
   (`lock GPS`). The UART source is `noselect`, so it never competes with PPS.
4. Kingfisher starts after chrony is healthy and reports live Pi-vs-GPS clock
   health in the cockpit header.

Repo-owned starting points live in `deploy/time-sync/`:

- `deploy/time-sync/gpsd.default.example` → `/etc/default/gpsd`
- `deploy/time-sync/chrony.conf.example` → `/etc/chrony/chrony.conf`
- `deploy/time-sync/99-pps-chrony.rules` → only if using kernel `refclock PPS`
  (not recommended here; see below)
- `deploy/time-sync/kingfisher-clock-check.sh` → `/usr/local/bin/` and
  `deploy/time-sync/kingfisher-clock-check.service` → `/etc/systemd/system/`
  (boot-time wrong-second check; see §8a)
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
makestep 0.5 3
rtcsync

# Offline-first flight recording: leave pool commented out.
#pool pool.ntp.org iburst

# UART-only: GPS serial is the only source, so it stays selectable. offset 0.0 —
# the Pi RTC battery keeps coarse boot time; the serial source carries time-of-day.
refclock SOCK /run/chrony.clk.ttyAMA0.sock refid GPS precision 1e-1 offset 0.0 delay 0.1 poll 2 filter 3
```

### UART + PPS (recommended)

```conf
makestep 0.5 3
rtcsync

#pool pool.ntp.org iburst

# GPS serial = time-of-day only, marked `noselect`: it can never be selected or
# step the clock; it only tells PPS which UTC second each pulse is (`lock GPS`).
# PPS (sub-µs, always correct) is the sole timekeeper. Keep offset 0.0.
refclock SOCK /run/chrony.clk.ttyAMA0.sock refid GPS precision 1e-1 offset 0.0 delay 0.1 poll 2 filter 3 noselect
refclock SOCK /run/chrony.pps0.sock refid PPS precision 1e-7 lock GPS poll 2
```

Directive notes:

- **`noselect` (GPS)** — the key directive. The gpsd serial time-of-day source has
  a latency relative to the true second that **varies from boot to boot** (~0 on
  some boots, ~0.6 s on others — the receiver's reported fix epoch wanders). If GPS
  and PPS are both selectable, a boot where they disagree leaves chrony unable to
  pick a truechimer: both go **`#x`** (“`Can't synchronise: no majority`”) and the
  deadlock never clears — the classic "sometimes locks in seconds, sometimes takes
  hours." Marking GPS `noselect` removes it from selection entirely; it only
  supplies the time-of-day that PPS `lock GPS` uses to number each second, and PPS
  becomes the sole, always-correct timekeeper.
- **`makestep 0.5 3`** — at boot, step (not slew) when the clock is more than
  **0.5 s** out, for the first **3** updates; slew-only afterward. Stepping lets PPS
  snap the clock to true at boot; limiting to 3 updates prevents a backward clock
  **jump mid-flight** (e.g. on a brief PPS glitch) that would corrupt logged
  timestamps.
- **`offset 0.0`** — leave it at zero. With GPS `noselect` the serial latency no
  longer competes with PPS, so there is **nothing to tune** (the old per-receiver
  `offset` calibration is obsolete — see §5). Zero keeps the serial time honest,
  which matters for correct second-numbering and for the wrong-second cross-check
  (`kingfisher-clock-check`, §8a).
- **`lock GPS`** — PPS steers the clock but needs the GPS refclock for which
  UTC second each pulse belongs to.
- **`poll 2 filter 3`** — faster boot lock. The default refclock `poll` is 4
  (one sample / 16 s), so GPS takes minutes to become selectable and PPS
  (`lock GPS`) longer still. `poll 2` (4 s) plus `filter 3` (median of the
  per-poll samples) lets GPS converge and PPS lock within tens of seconds once
  a fix exists. Keep PPS `poll` equal to GPS `poll` so `lock GPS` always has a
  recent time-of-day sample. GPS serial only carries time-of-day, so the lower
  poll does not degrade steady-state accuracy — PPS still steers the clock.
  Note: the bigger boot delay after days off is usually GPS **acquisition**
  (cold start); keep the receiver's backup battery (V_BCKP) powered for a
  warm/hot start to cut that down — chrony tuning cannot speed up acquisition.
- **`pool`** — fine on the bench with internet; for offline recording, comment
  it out so chrony selects GPS/PPS instead of NTP.

Use **SOCK PPS** (above). Do **not** also add `refclock PPS /dev/pps0`: gpsd
opens `/dev/pps0` when a GPS is present, and chrony cannot share the device.
Kernel `refclock PPS` would need `deploy/time-sync/99-pps-chrony.rules` and a
gpsd-free PPS path — not worth it on this deployment.

On systems that use `/var/run` instead of `/run`, adjust SOCK paths to match
what chronyd creates.

### Automatic boot (RTC battery + airplane sit)

With a Pi RTC battery, the host boots with roughly correct **date/time**. chrony
then locks without manual steps:

1. **PPS + `lock GPS`** (recommended): once gpsd has a 3D fix, chrony selects
   **`#* PPS`** and disciplines to sub-µs. GPS serial stays **`#? GPS`** by design
   (`noselect`) — it only numbers the PPS second.
2. **UART-only**: GPS serial auto-locks at **`offset 0.0`** within ~1 min of 3D
   fix (ms-level accuracy — often enough for IIO/pod fusion).

The old failure mode — both sources stuck **`#x`** (“no majority”) for minutes to
hours on some boots — was the GPS/PPS disagreement described above; **`noselect`**
on the GPS refclock eliminates it.

**Do not omit gpsd/GPS entirely.** Kingfisher still needs the receiver for fixes;
chrony only chooses which gpsd SOCK feeds discipline. Even “PPS-only” chrony
(below) still requires a GPS fix so gpsd can tag PPS pulses with the correct UTC
second.

Expected unattended timeline after power-on (outdoor/hangar with sky view):

| Phase | Duration | What happens |
|-------|----------|--------------|
| Boot | 0–30 s | RTC sets coarse wall clock; gpsd + chronyd start |
| GNSS acquisition | 1–5 min | Cold start if M9N V_BCKP flat; faster with receiver backup |
| chrony lock | ~30–90 s after 3D fix | `#* PPS` (or `#* GPS` UART-only) without manual steps |

### Alternative chrony profiles

| Profile | chrony refclocks | Accuracy | Notes |
|---------|------------------|----------|-------|
| **UART + PPS** (default) | GPS SOCK `noselect` + PPS SOCK `lock GPS` | sub-µs | Best; PPS is sole timekeeper, GPS only numbers the second |
| **PPS-only** | PPS SOCK only (no `lock GPS`) | sub-µs | gpsd still needs a fix to tag PPS with the UTC second |
| **UART-only** | GPS SOCK only (selectable) | ~ms | Simplest; use `offset 0.0` with RTC battery |

See commented alternatives in `deploy/time-sync/chrony.conf.example`.

**Omitting the GPS chrony refclock** is feasible when PPS is wired and the RTC
keeps coarse time within ~0.5 s. **Omitting gpsd/GPS hardware** is not — you
lose navigation data and PPS second tagging.

## 5. The GPS `offset` (no longer tuned)

Earlier setups tuned a per-receiver `offset` so the GPS serial time would agree
with PPS. That is **obsolete**: the serial latency varies from boot to boot, so no
fixed `offset` works, and chasing it was the actual cause of the intermittent-lock
problem. With GPS marked **`noselect`** (§4) the serial source no longer competes
with PPS for selection, so there is nothing to calibrate.

**Leave `offset 0.0`.** Zero keeps the serial time-of-day honest, which is what
PPS `lock GPS` needs to number each second correctly and what
`kingfisher-clock-check` (§8a) uses to catch a wrong-second lock.

> `chronyc sourcestats` will show the GPS **`Offset`** as roughly the serial
> latency (often a few hundred ms, and it differs between boots). With `noselect`
> that is **expected and harmless** — it is not an error to correct.

For UART-only (no PPS) the GPS source *is* selected, but `offset 0.0` with the Pi
RTC battery already lands within tens of ms — still no tuning needed.

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

Healthy PPS deployment (pool commented out):

- GPS and PPS **Reach** non-zero in `chronyc sources -v`
- **`#* PPS`** — chrony disciplining from PPS
- **`#? GPS`** — serial source present but `noselect` (time-of-day only via
  `lock GPS`); the `?` is **expected here, not an error**
- GPS **`sourcestats` Offset** ≈ serial latency (a few hundred ms is fine with
  `noselect`); PPS offset in µs

See `deploy/time-sync/verify.md` for the kingfisher UI checks.

## 8. Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| GPS/PPS `#?`, Reach `0`, `sourcestats` NP `0` | `gpsd.service` not running — `systemctl enable --now gpsd.service` |
| Reach `0` after editing chrony | Restart **`gpsd`** after **`chronyd`** |
| gpsd log: `Transport endpoint is not connected` | Same — stale SOCK after chrony restart |
| **`#? GPS` with Reach `377`** | **Normal** — GPS serial is `noselect` (time-of-day only); PPS should be `#*` |
| NTP `*` instead of PPS `*` | **`pool`** enabled while online — comment out for offline |
| PPS Reach `0` | `/dev/pps0` missing (no overlay/reboot), or not in gpsd **`DEVICES`** |
| `#x` on PPS/GPS, `Can't synchronise: no majority` | Pre-`noselect` config (GPS still selectable and disagreeing with PPS) — add **`noselect`** to the GPS refclock (§4) |
| Clock off by ~1 s after lock (wrong second) | PPS locked to the wrong UTC second — see `kingfisher-clock-check` (§8a); check GPS fix/almanac and RTC sanity |
| `cgps` OK, chrony dead | Almost always gpsd not feeding SOCK — restart pair above |

## 8a. Boot-time wrong-second check (`kingfisher-clock-check`)

Once chrony is PPS-disciplined the clock is correct to sub-µs, so the only
realistic failure left is an **integer-second mislock**: gpsd/chrony tagging the
PPS edge to the wrong UTC second (e.g. on a marginal fix at boot). That error is
internally consistent — PPS and the system clock agree on the *wrong* second — so
it can't be caught by comparing them to each other.

`kingfisher-clock-check` (a system oneshot, run once at boot after chrony/gpsd)
catches it by cross-checking the **`noselect` GPS source's offset**, whose UTC
comes straight from the satellite navigation message and is therefore independent
of the PPS second-numbering. Normal is roughly the serial latency (~0–0.65 s); a
shift of about ±1 s (outside the band `[-0.2, 0.9]` s) means a probable
wrong-second lock. It **logs to the journal and never changes the clock.**

Install:

```bash
sudo install -m 755 deploy/time-sync/kingfisher-clock-check.sh /usr/local/bin/
sudo install -m 644 deploy/time-sync/kingfisher-clock-check.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kingfisher-clock-check.service
```

Check after a boot:

```bash
journalctl -b -u kingfisher-clock-check
# OK:   ... GPS cross-check offset 0.61s within latency band — second-numbering looks correct
# WARN: ... possible WRONG-SECOND clock error! ...
```

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

When chrony shows **Time unsynced**, open the status drawer: kingfisher auto-runs
`chronyc reselect` on a cooldown (config `clock.auto_resync`; requires passwordless
`sudo chronyc` — see `deploy/time-sync/verify.md` §6), offers **Retry sync**
manually, and **Restart time services** when the deploy helper is installed.
**Restart time services** does the ordered `chronyd`→`gpsd` restart plus a
`reselect` to recover a stale SOCK connection or a GPS/PPS falseticker state. (It
no longer rewrites the GPS `offset`: with GPS `noselect` there is nothing to tune
— see §5.) These do **not** substitute for getting a clean fix; if the clock is
off by ~1 s, that is a wrong-second lock, not an offset problem (§8a).

For what each flight DB **`ts_ns`** column means (including GPS row time vs
**`fix_time_unix_s`**), see **`docs/timestamps.md`**.

## 10. Kingfisher startup ordering

Kingfisher does **not** wait for chrony — it starts (UI + recording) immediately
on the RTC clock. The old `kingfisher-prestart.sh` `waitsync` gate is retired: it
cost up to ~120 s of unreachable UI at engine start for no data benefit. Instead:

- A short Pi-vs-GPS assessment still runs at startup (`/api/status`, cockpit
  header) and is recorded in flight-DB metadata. Its logged `gps_lag+offset`
  *includes* the ~0.6–0.7 s gpsd pipeline lag — ~1 s is normal, not clock error.
- A red cockpit banner shows **CLOCK NOT SYNCED** until chrony locks.
- A hybrid clock watcher (kernel `timerfd` `TFD_TIMER_CANCEL_ON_SET` step
  listener + 1 Hz slew sampler) appends any realtime↔monotonic mapping shift
  ≥ 20 ms to the flight DB's `clock_offsets` table, so all earlier timestamps
  are exactly correctable post-flight (see `docs/timestamps.md`).
- chrony is configured `makestep 0.2 -1` + `maxslewrate 1000`: corrections
  > 0.2 s are applied as one discrete logged **step** (analysis-friendly)
  rather than a long fast slew; residual slews distort intervals ≤ 0.1 %.
- A dead RTC (clock reads 1970) only changes the DB *filename* to a fallback
  `unsynced_NNNN_<tail>.db`, renamed at close once true time is learned.

Kingfisher runs as a **user** unit, which cannot order against system units, so
`After=chronyd.service gpsd.service` would be a no-op and is omitted — and no
longer needed.

See **`deploy/systemd/kingfisher.service.example`** for the full user unit.
Kingfisher's status model still reports if the clock later drifts or goes stale.
