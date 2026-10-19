# Time-Sync Verification

Use after configuring `gpsd` and `chrony` per `docs/time-sync.md`.

## 1. gpsd sees the receiver

```bash
systemctl is-active gpsd    # must be active, not just gpsd.socket
cgps -s
```

Check that:

- the device is `/dev/ttyAMA0`
- fixes keep updating
- GPS time is non-zero

## 2. PPS (if wired)

```bash
ls -l /dev/pps0
sudo ppstest /dev/pps0
gpsmon    # look for "PPS offset:" near sub-millisecond
```

## 3. chrony is using GPS / PPS

```bash
chronyc tracking
chronyc sources -v
chronyc sourcestats
```

Check that:

- GPS and PPS have non-zero **Reach** (octal column in `sources -v`)
- with pool commented out: **`#* PPS`** within ~1 min of 3D fix, and **`#? GPS`**
  (serial source present but `noselect` — the `?` is **expected**, not an error)
- GPS **`sourcestats` Offset** ≈ serial latency (a few hundred ms is fine with
  `noselect`); PPS offset in µs
- `Leap status` is normal after lock

If Reach stays 0 after a chrony restart:

```bash
sudo systemctl restart chronyd && sleep 2 && sudo systemctl restart gpsd
```

## 4. kingfisher agrees

Open the cockpit UI and confirm the header clock badge shows:

- a fresh GPS fix age
- skew under about 250 ms once settled (fix-epoch lag ~650 ms is normal)
- no persistent `startup fallback` warning after a clean synchronized boot

### Boot-time wrong-second check

If `kingfisher-clock-check` is installed (see `docs/time-sync.md` §8a):

```bash
journalctl -b -u kingfisher-clock-check
```

Expect `OK: ... within latency band — second-numbering looks correct`. A
`WARN: ... possible WRONG-SECOND clock error!` means PPS locked to the wrong UTC
second (check the GPS fix/almanac and RTC sanity) — it never changes the clock.

## 5. Troubleshooting

See the table in `docs/time-sync.md`. Common fixes:

- enable and start `gpsd.service` at boot
- restart `gpsd` after every `chronyd` restart
- if both GPS and PPS sit `#x` (“no majority”), ensure the GPS refclock has
  `noselect` (`docs/time-sync.md` §4) — do **not** tune `offset`
- comment `pool pool.ntp.org` for offline GPS/PPS discipline

## 6. In-flight resync helper (optional)

For the cockpit **Restart time services** button (`POST /api/clock/resync` level
`full`), install the helper and grant passwordless sudo to the kingfisher runtime
user:

```bash
sudo install -m 755 deploy/time-sync/kingfisher-resync-time.sh /usr/local/bin/
sudo tee /etc/sudoers.d/kingfisher-resync <<'EOF'
eric ALL=(root) NOPASSWD: /usr/local/bin/kingfisher-resync-time.sh, /usr/bin/chronyc
EOF
sudo visudo -cf /etc/sudoers.d/kingfisher-resync
```

Set `clock.resync_helper` in `~/.config/kingfisher/config.json` if you use a
non-default path. **Retry sync** (light) uses `chronyc reselect` only and needs no
sudo. Auto-retry runs the light path on a cooldown when unsynced with a fresh GPS
fix.

## 7. Timesync clock-floor touch (recommended if the RTC doesn't hold time)

This box uses chrony, not `systemd-timesyncd`, so nothing normally advances
`/var/lib/systemd/timesync/clock` — the file systemd uses as a "never step the
clock backward past this" floor. If the RTC isn't retaining time across power
cycles, every boot's pre-GPS-lock window (~15-20s) falls back to that floor
instead of a recent time, and it can be stale by weeks instead of minutes. See
`deploy/time-sync/kingfisher-timesync-floor-touch.sh` for the full story.

```bash
sudo install -m 755 deploy/time-sync/kingfisher-timesync-floor-touch.sh      /usr/local/bin/
sudo install -m 644 deploy/time-sync/kingfisher-timesync-floor-touch.service /etc/systemd/system/
sudo install -m 644 deploy/time-sync/kingfisher-timesync-floor-touch.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kingfisher-timesync-floor-touch.timer
```

Verify:

```bash
sudo systemctl start kingfisher-timesync-floor-touch.service
journalctl -u kingfisher-timesync-floor-touch.service -n 5 --no-pager
# Expect: "floor advanced to <UTC time> (synced to #* PPS)" once chrony has locked.
stat -c '%y' /var/lib/systemd/timesync/clock   # mtime should track "now", refreshed every ~15 min
```
