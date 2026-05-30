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
- with pool commented out and offset tuned: **`#* PPS`**, **`#- GPS`**
- GPS **`sourcestats` Offset** is near zero (not hundreds of ms)
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

## 5. Troubleshooting

See the table in `docs/time-sync.md`. Common fixes:

- enable and start `gpsd.service` at boot
- restart `gpsd` after every `chronyd` restart
- tune GPS `offset` with the NTP cross-check (same sign as `sourcestats`, do not negate)
- comment `pool pool.ntp.org` for offline GPS/PPS discipline
