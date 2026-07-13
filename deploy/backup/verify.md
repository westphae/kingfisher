# Flight-data backup — setup & verification

Pushes closed flight DBs from the Pi (`~/kingfisher/flights`) to the home NAS
whenever the Pi is on the home LAN. The aircraft has ~6 GB of irreplaceable flight
data on a single device; this closes that gap without any manual `rsync`.

- **NAS:** an **Asustor AS5304T (ADM)** at `192.168.86.40`, share **`Public` →
  `/volume1/Public`**, target `Public/data/kingfisher/flights`. (The ADM web admin
  at `https://192.168.86.40:5679` is unrelated to the backup transport.)
- **Transport (config `TRANSPORT`):** `rsyncd` (ADM Rsync Server — the "rsync" you
  enabled; Asustor's supported path, no shell account, scoped to the share) or
  `ssh` (rsync over SSH — encrypted + key-based, but ADM restricts admin SSH so it
  may refuse with "connection closed"/"not allowed at this time"). **Default and
  recommended: `rsyncd`.**
- **Triggers:** NM dispatcher on **wlan1 up** (primary) + an **hourly timer**
  fallback. Both no-op when wlan1 has no address (i.e. in the aircraft) or the NAS
  is unreachable.
- **Safety:** additive only (no `--delete`); the live DB and every `-wal`/`-shm`
  sidecar are excluded, so only closed, consistent DBs are copied. Runs `Nice=10 /
  idle` I/O so it never contends with the recorder.

## Files

| File | Installs to |
|------|-------------|
| `kingfisher-flight-backup.sh` | `/usr/local/bin/` (0755) |
| `kingfisher-backup.conf.example` | `/etc/kingfisher-backup.conf` (0640 root:eric) |
| `kingfisher-flight-backup.service` | `/etc/systemd/system/` (0644) |
| `kingfisher-flight-backup.timer` | `/etc/systemd/system/` (0644) |
| `90-kingfisher-backup` | `/etc/NetworkManager/dispatcher.d/` (0755, root) |

## 1. NAS side (one-time, in ADM)

1. **Create the target folder.** ADM → File Explorer → under **Public** create
   `data/kingfisher/flights` (on disk: `/volume1/Public/data/kingfisher/flights`).

Then set up **one** transport:

### Option A — `rsyncd` (recommended)

2. ADM → **Backup & Restore → Rsync Server** → enable it.
3. Add a backup user (e.g. `kingfisher`) with a password, and grant it the module
   that maps to the **Public** share. Note the **module name** — set `RSYNC_MODULE`
   and `RSYNC_USER` in the conf to match (defaults assume module `Public`).
4. On the Pi, drop the password into the secret file (referenced by `RSYNC_SECRET`):
   ```sh
   mkdir -p ~/.config/kingfisher
   printf '%s' 'THE_RSYNC_PASSWORD' > ~/.config/kingfisher/backup.secret
   chmod 600 ~/.config/kingfisher/backup.secret     # rsync refuses it otherwise
   ```

### Option B — `ssh`

2. ADM → **Services → Terminal & SNMP → SSH** → enable, port 22 (Administrators
   group only; use `admin` or another admin account as `SSH_USER`).
3. Generate + authorize a dedicated key on the Pi:
   ```sh
   ssh-keygen -t ed25519 -N '' -C 'kingfisher-flight-backup' -f ~/.ssh/kingfisher_backup
   ssh-copy-id -i ~/.ssh/kingfisher_backup.pub -p 22 admin@192.168.86.40
   ssh -i ~/.ssh/kingfisher_backup admin@192.168.86.40 'ls -ld /volume1/Public/data/kingfisher/flights'
   ```
   Optionally harden: prefix the new `authorized_keys` line on the NAS with
   `from="192.168.86.151",restrict ` so the key works only from the Pi's wlan1 IP
   and can't open a shell.
   > If SSH returns "connection closed"/"not allowed at this time", ADM is
   > restricting admin SSH — use Option A.

## 2. Pi side — install

```sh
cd ~/go/src/github.com/westphae/kingfisher
sudo install -m 755 deploy/backup/kingfisher-flight-backup.sh    /usr/local/bin/
sudo install -m 640 deploy/backup/kingfisher-backup.conf.example /etc/kingfisher-backup.conf
sudo chown root:eric /etc/kingfisher-backup.conf
sudo install -m 644 deploy/backup/kingfisher-flight-backup.service /etc/systemd/system/
sudo install -m 644 deploy/backup/kingfisher-flight-backup.timer   /etc/systemd/system/
sudo install -m 755 deploy/backup/90-kingfisher-backup /etc/NetworkManager/dispatcher.d/

sudoedit /etc/kingfisher-backup.conf      # set TRANSPORT + fill that block
sudo systemctl daemon-reload
sudo systemctl enable --now kingfisher-flight-backup.timer
```

## 3. Verify

```sh
# Manual run (must be on the home LAN, wlan1 up):
sudo systemctl start kingfisher-flight-backup.service
journalctl -u kingfisher-flight-backup.service -n 40 --no-pager
# Expect: "excluding active DB <name>" (if recording), then "backup complete".

# Confirm files landed (rsyncd):
rsync rsync://kingfisher@192.168.86.40/Public/data/kingfisher/flights/ \
  --password-file ~/.config/kingfisher/backup.secret | tail
# ...or (ssh):
ssh -i ~/.ssh/kingfisher_backup admin@192.168.86.40 'ls /volume1/Public/data/kingfisher/flights | tail'

# Timer scheduled?
systemctl list-timers kingfisher-flight-backup.timer --no-pager

# Dispatcher wired:
sudo /etc/NetworkManager/dispatcher.d/90-kingfisher-backup wlan1 up
journalctl -u kingfisher-flight-backup.service -n 10 --no-pager
```

### No-op cases (all expected, logged, exit 0)

- **In the aircraft** (`wlan1` down): `"wlan1 has no global address; not home — skipping"`.
- **NAS off/unreachable:** `"192.168.86.40:<port> unreachable; skipping"`.
- **Overlapping run:** `"another backup is already running; skipping"`.

### Notes

- **No `--delete`:** the NAS keeps every DB ever pushed even after the Pi's local
  copy is cleaned. Prune the NAS side manually if it ever grows too large.
- **Active DB:** the DB kingfisher currently holds open is skipped (detected from
  `/proc/<pid>/fd`, else the newest `.db`) and is picked up on the next run once
  the flight closes. Sidecars are never copied, so closed DBs are consistent
  snapshots (the store's startup sweep + graceful-close cleanup keep closed DBs
  sidecar-free anyway).
- **First sync** copies the whole backlog and may take a while over wifi;
  `--partial` makes it resumable across dropped connections.
