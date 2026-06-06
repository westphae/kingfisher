# Cockpit power off

The header **OFF** button calls `POST /api/power/off`, which pauses recording
(flush + WAL checkpoint), shuts down kingfisher cleanly, then runs
`/usr/local/bin/kingfisher-poweroff.sh` to `sync` and `systemctl poweroff`.

## Install helper

```bash
sudo install -m 755 deploy/kingfisher-poweroff.sh /usr/local/bin/
sudo tee /etc/sudoers.d/kingfisher-poweroff <<'EOF'
eric ALL=(root) NOPASSWD: /usr/local/bin/kingfisher-poweroff.sh
EOF
sudo visudo -cf /etc/sudoers.d/kingfisher-poweroff
```

Replace `eric` with your Pi login user. Optional override in `config.json`:

```json
"clock": {
  "poweroff_helper": "/usr/local/bin/kingfisher-poweroff.sh"
}
```

## microSD hygiene

- Prefer **OFF** (or `systemctl --user stop kingfisher` then `sudo poweroff`) over
  cutting aircraft power while the Pi is running.
- Kingfisher checkpoints SQLite WAL on pause and on DB close; the helper runs
  `sync` twice before poweroff.
- Use a quality card; keep free space on the boot volume (status drawer shows DB
  volume free bytes).
- After unclean power loss, run `fsck` on the card before the next flight if
  the Pi fails to boot or SQLite reports corruption.

The user systemd unit example sets `TimeoutStopSec=45` so SIGTERM shutdown has
time to flush before systemd sends SIGKILL.
