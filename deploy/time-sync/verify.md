# Time-Sync Verification

Use this after configuring `gpsd` and `chrony`.

## 1. gpsd sees the receiver

```bash
cgps -s
```

Check that:

- the device is `/dev/ttyAMA0`
- fixes keep updating
- GPS time is non-zero

## 2. chrony is tracking the GPS SOCK source

```bash
chronyc tracking
chronyc sources -v
```

Check that:

- the GPS source is present and reachable
- `Leap status` is normal after lock
- `Last offset` is no longer seconds away from zero

## 3. kingfisher agrees

Open the cockpit UI and confirm the header clock badge shows:

- a fresh GPS fix age
- a small Pi-vs-GPS offset
- no persistent `startup fallback` warning after a clean synchronized boot

## 4. Troubleshooting

If chrony never sees the GPS source:

- make sure `chronyd` started before `gpsd`
- confirm the configured SOCK path matches your system's runtime dir
- verify `gpsd` is actually reading `/dev/ttyAMA0`
- keep `-n -b` enabled
