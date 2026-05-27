# GNSS Time Sync

Kingfisher records everything against the host's `CLOCK_REALTIME` time base. The
flight DB filename, `_session.start_time`, buffered IIO timestamps on
`current_timestamp_clock == realtime`, pod wall-clock reconstruction, and
derived-stream timestamps all assume the Pi wall clock is already sane.

The intended deployment model is:

1. `gpsd` reads the M9N on `/dev/ttyAMA0` in read-only mode.
2. `chronyd` disciplines the Pi clock from gpsd over a Unix-domain SOCK refclock.
3. Kingfisher starts after chrony is up, and reports live Pi-vs-GPS clock health.

## UART-Only Setup

Pi 5 UART wiring stays on GPIO 14/15 (`/dev/ttyAMA0`). Use the repo-owned
examples in `deploy/time-sync/` as the starting point:

- `deploy/time-sync/gpsd.default.example`
- `deploy/time-sync/chrony.conf.example`
- `deploy/time-sync/verify.md`

Install the services:

```bash
sudo apt install gpsd gpsd-clients chrony
```

Copy the example gpsd defaults into `/etc/default/gpsd`, then ensure gpsd keeps
the receiver read-only and opens the UART immediately:

```ini
DEVICES="/dev/ttyAMA0"
GPSD_OPTIONS="-n -b -s 115200"
USBAUTO="false"
```

- `-n` is important for time service; gpsd must be polling even when no client UI
  is connected.
- `-b` keeps gpsd from reconfiguring the receiver and preserves the lean
  NAV-PVT stream used elsewhere in kingfisher.
- `-s 115200` is required if the M9N UART baud was saved at 115200 (see README
  GPS section). With `-b`, gpsd does not autobaud; without `-s` it stays at 19200
  and you get no fix — `cgps` and kingfisher will look dead even though gpsd is running.

For chrony, prefer the gpsd SOCK integration over SHM. Recent gpsd uses a
dedicated serial-time socket named `chrony.clk.<device>.sock`; for the Pi 5 UART
that means `chrony.clk.ttyAMA0.sock`.

Use a chrony refclock block like:

```conf
makestep 1.0 3
rtcsync
# Offline-first: leave pool lines commented out (see below).
refclock SOCK /run/chrony.clk.ttyAMA0.sock refid GPS precision 1e-1 offset -0.44 delay 0.1
```

- `makestep 1.0 3` implements the boot policy from the plan: allow large steps
  early at boot, then slew afterward.
- Tune the GPS `offset` with `chronyc sourcestats` after a 3D fix. If GPS shows
  about `+440ms`, try `offset -0.44` (seconds added to the gpsd SOCK timestamp).
- The `0.9999` value shown in some gpsd examples is a placeholder, not a good
  production default for UART-only discipline.
- Start `chronyd` before `gpsd` so the socket exists before gpsd connects.
- If `pool pool.ntp.org` is enabled and the Pi is online, chrony may prefer NTP
  and mark the GPS refclock as `#x` (in error). That is fine on the bench with
  internet, but for offline flight recording comment the pool line out.

On systems that use `/var/run` or `/tmp` instead of `/run`, adjust the SOCK path
to match what chronyd actually creates.

## Startup Ordering

Kingfisher now performs a short startup assessment of Pi-vs-GPS time before it
opens the flight DB, and it exposes that result in `/api/status` and the cockpit
header. That is an assessment, not a hard block.

To protect DB filenames and session start times on cold boot, the deployment
should still order kingfisher after chrony is healthy. A practical systemd
pattern is:

```ini
[Unit]
After=chronyd.service gpsd.service
Wants=chronyd.service gpsd.service

[Service]
ExecStartPre=/usr/bin/chronyc waitsync 20 0.25
```

That keeps the service from starting until chrony reports sync, while
kingfisher's own status model still tells you whether the clock later drifted,
went stale, or started unsynchronized anyway.

## Expected Accuracy

Without PPS, this is a coarse-but-useful wall-clock discipline path:

- Seconds-level errors should disappear once chrony locks to gpsd.
- Sub-second alignment is realistic.
- Do not expect true sub-millisecond timestamp accuracy from UART-only time.

### Kingfisher clock badge vs chrony

The cockpit header compares host wall time to the **fix epoch** inside each gpsd
TPV report. On this u-blox binary path that lag is often **600–700 ms** even when
chrony and NTP are correct — it is receiver/pipeline delay, not a broken Pi
clock.

Kingfisher therefore tracks:

- **Fix epoch lag** — raw offset (often ~650 ms here)
- **Skew** — offset minus a running median baseline (true clock error once settled)

"Clock OK" means fresh fixes and skew under about 250 ms, not that lag is zero.
Use `chronyc tracking` and `chronyc sources -v` to confirm chrony is actually
using the GPS refclock when offline.

## Verification

Use the checklist in `deploy/time-sync/verify.md`. The short version is:

```bash
cgps -s
chronyc tracking
chronyc sources -v
```

You want to see:

- gpsd reporting valid TPV updates on `/dev/ttyAMA0`
- chrony showing the GPS SOCK source reachable
- the cockpit header's clock badge showing a fresh fix and a small offset

## PPS Follow-On

When PPS is wired in a later hardware revision, reserve **GPIO 18** (physical pin
12) as the preferred input:

```txt
dtoverlay=pps-gpio,gpiopin=18
```

Then verify `/dev/pps0`, add a PPS SOCK refclock in chrony, and keep the GPS
serial SOCK as the coarse lock source:

```conf
refclock SOCK /run/chrony.clk.ttyAMA0.sock refid GPS precision 1e-1 offset 0.2 delay 0.1
refclock SOCK /run/chrony.pps0.sock refid PPS precision 1e-7 lock GPS
```

The serial source stays useful even after PPS lands; PPS sharpens the clock,
while the UART stream supplies time-of-day.
