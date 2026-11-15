# JPI auto-ingest

The Pi merges EDM-930 downloads into its own flight databases. The NAS is
only a place Eric drops `UYYMMDD.JPI`. This timer copies those files into
`~/kingfisher/engine/raw/` and then runs the merge. It does not write a
database that lives on the NAS.

The flight-backup timer (`deploy/backup/`) pushes flight DBs to an Asustor
at `192.168.86.40`. The JPI archive Eric named is
`/volume1/Public/archive/Documents/Bonanza/JPI/`. Those may be the same
machine and they may not. The config leaves the host empty until that is
settled. `TRANSPORT=none` is a valid steady state: drop files straight into
`~/kingfisher/engine/raw/` and the hourly run still merges them.

## What the timer does

1. If `TRANSPORT` is `rsyncd` or `ssh` and the host answers, `rsync` copies
   `U*.JPI` (and `.DAT`) into `~/kingfisher/engine/raw/`. No `--delete`.
   The NAS is not modified. A missing secret, a missing interface address,
   or an unreachable host is a skip, not a failed unit.
2. `analyze_flights.py jpi-auto` fits every local flight DB that has no
   `jpi_edm930` rows, or whose source file bytes changed. A fit is written
   only when the existing quality gates pass (path, fix count, median
   error, distinct peak). A refused fit keeps any previous merge.
3. `analyze_flights.py engine --all` refreshes `analysis-cache/engine/`.

Every attempt is a row in `~/kingfisher/engine/ingest.json`: file hash, JPI
flight id, database, offset, drift, residual, and applied / refused / kept
plus the reason. Running it again on the same bytes bumps that row's count
instead of appending forever. Suspected duplicate sessions (same GPS row
count and max groundspeed) and `clock_offsets` steps of a minute or more
are flags in the same file. A flag does not block a merge.

Manual equivalent:

```bash
uv run --project analysis python scripts/analyze_flights.py jpi-auto --dry-run
uv run --project analysis python scripts/analyze_flights.py jpi-auto
uv run --project analysis python scripts/analyze_flights.py engine --all
```

`jpi` without `-auto` is still the one-flight command. Dry-run is its
default. `jpi-auto` applies when the gates pass; `--dry-run` only records.

## NAS access this does not assume

Pick one. Do not point the script at a writable share.

**Read-only rsync module.** On the NAS, export the JPI directory as a
module that this user can read and cannot write. Put the password in
`/home/eric/.config/kingfisher/jpi.secret` (`chmod 600`). Set
`TRANSPORT=rsyncd`, `NAS_HOST`, `RSYNC_MODULE`, and `RSYNC_MODULE_PATH`.

**Read-only SSH key.** A key that can `rsync` from
`NAS_PATH` and cannot write there. `TRANSPORT=ssh`, `NAS_HOST`, `SSH_KEY`,
`NAS_PATH`.

The pull runs only when `REQUIRE_IFACE` (default `wlan1`) has an address,
same idea as the flight backup: not from the airplane.

## Install

```bash
sudo install -m 755 deploy/jpi/kingfisher-jpi-ingest.sh /usr/local/bin/
sudo install -m 644 deploy/jpi/kingfisher-jpi-ingest.service /etc/systemd/system/
sudo install -m 644 deploy/jpi/kingfisher-jpi-ingest.timer /etc/systemd/system/
sudo cp deploy/jpi/kingfisher-jpi-ingest.conf.example /etc/kingfisher-jpi-ingest.conf
sudo systemctl daemon-reload
sudo systemctl enable --now kingfisher-jpi-ingest.timer
```

One shot: `sudo systemctl start kingfisher-jpi-ingest.service`

The oneshot runs as `eric`, so it can read `~/kingfisher` and the repo
checkout at `REPO`. `TimeoutStartSec=infinity` because a directory of
downloads can take a while; `flock` keeps two runs from stacking.

Thresholds Eric tunes live in `~/kingfisher/engine/limits.json`. See
`docs/analysis/engine.md`.
