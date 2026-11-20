# JPI auto-ingest

The Pi merges EDM-930 downloads into its own flight databases. The NAS is
only a place Eric drops `UYYMMDD.JPI`. This timer copies those files into
`~/kingfisher/engine/raw/` and then runs the merge. It does not write a
database that lives on the NAS, and it does not fetch the flights folder.
Flight databases stay on the Pi; `deploy/backup/` is what pushes them.

NAS access is a restricted SSH key on the Pi at `~/.ssh/nas_ro`. The NAS
account is forced to `rrsync -ro` rooted at the JPI folder, so the rsync
source is `eric@192.168.86.40:` with no path after the colon. The key is
named by path in `~/.config/kingfisher/jpi.env`. That file lives outside
the git checkout. The key, its `.pub`, and the NAS host key are not in
the repo.

`TRANSPORT=none` (the script default, before `jpi.env` is installed) is a
valid steady state: drop files straight into `~/kingfisher/engine/raw/`
and the hourly run still merges them.

## What the timer does

1. If `TRANSPORT=ssh` and the host answers, `rsync` copies `U*.JPI` into
   `~/kingfisher/engine/raw/`. The command is:

   ```bash
   rsync -a --partial --include='U*.JPI' --exclude='*' \
     -e 'ssh -i ~/.ssh/nas_ro -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10' \
     eric@192.168.86.40: ~/kingfisher/engine/raw/
   ```

   There is no `--delete`. A missing key, a missing `wlan1` address, or an
   unreachable host is a skip, not a failed unit. `ConnectTimeout=10` is
   there so a host that accepts TCP and then stalls cannot hang the oneshot.
2. `analyze_flights.py jpi-auto` merges new or changed JPI files into the
   flight DBs they overlap. It first checks the ledger for file and DB
   mtime/size (and SHA when a file has changed). If nothing is new, it
   does not hash, parse, or open any flight DB and finishes in seconds.
   A new or changed JPI file only opens the DBs whose time span overlaps
   that file. A new or changed flight DB is still considered against the
   existing downloads. `--flights-dir` is a top-level option, so the
   oneshot passes it before `jpi-auto` and before `engine`. The
   subcommands accept it too, which is what an older copy of this script
   (the one in `/usr/local/bin`) still does. A fit is written only when
   the existing quality gates pass (path, fix count, median error,
   distinct peak). A refused fit keeps any previous merge. It opens one
   flight DB at a time. GPS and OAT are read at one sample per second,
   and a high-rate IMU temperature column is not loaded. A session the
   recorder still has open (newest file with a fresh `-wal`, or any
   non-empty `-wal` written in the last two minutes) is skipped.
   `--since YYYY-MM-DD` and `--limit N` bound a manual pass; the timer
   does not pass them.
3. `analyze_flights.py engine --changed` (the timer path) refreshes
   `analysis-cache/engine/` only for flights whose DB mtime/size, the
   limits fingerprint, or `REVIEWER_VERSION` changed. Unchanged flights
   are reused from the cache and `summary.json`. `engine --all` still
   forces a full re-review. Bump `analysis.engine.cache.REVIEWER_VERSION`
   after a reviewer code change so the next incremental run redoes every
   flight.

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
uv run --project analysis python scripts/analyze_flights.py engine --changed
uv run --project analysis python scripts/analyze_flights.py engine --all
```

`jpi` without `-auto` is still the one-flight command. Dry-run is its
default. `jpi-auto` applies when the gates pass; `--dry-run` only records.

## Python environment

`analysis` and `enclosures` share one uv workspace and one `.venv`.
CadQuery (the enclosure package) depends on vtk and trame. From the repo
root:

```bash
uv sync --all-packages
```

`uv sync --project analysis` makes that shared venv match the analysis
package only. uv then uninstalls the enclosure packages, including vtk
and trame. Do not use it on the Pi. `uv run --project analysis` is the
right way to run a command; it does not prune the other member.

## NAS key

The key stays at `~/.ssh/nas_ro` (`chmod 600`). `jpi.env` only stores that
path. Do not copy the key, a public key, or `known_hosts` into the
checkout. `scripts/check-secrets.sh` (and the pre-commit lint) fails if a
tracked file contains an OpenSSH private-key banner or an `ssh-ed25519`
public blob.

On the NAS, the forced command is `rrsync -ro` with its root at the JPI
folder. The Pi never gets a shell and cannot write. The one-time host-key
acceptance belongs in the Pi user's `~/.ssh/known_hosts`, not in git.

The pull runs only when `REQUIRE_IFACE` (default `wlan1`) has an address,
same idea as the flight backup: not from the airplane.

## Install

```bash
sudo install -m 755 deploy/jpi/kingfisher-jpi-ingest.sh /usr/local/bin/
sudo install -m 644 deploy/jpi/kingfisher-jpi-ingest.service /etc/systemd/system/
sudo install -m 644 deploy/jpi/kingfisher-jpi-ingest.timer /etc/systemd/system/
install -d -m 700 ~/.config/kingfisher
install -m 600 deploy/jpi/jpi.env.example ~/.config/kingfisher/jpi.env
sudo systemctl daemon-reload
sudo systemctl enable --now kingfisher-jpi-ingest.timer
```

`/etc/kingfisher-jpi-ingest.conf` is optional. When it exists it is sourced
after `jpi.env` and overrides it. Copy
`deploy/jpi/kingfisher-jpi-ingest.conf.example` only if you want that.

One shot: `sudo systemctl start kingfisher-jpi-ingest.service`

The oneshot runs as `eric`, so it can read `~/kingfisher`, `~/.ssh/nas_ro`,
and the repo checkout at `REPO`. `TimeoutStartSec=infinity` because a
directory of downloads can take a while; `flock` keeps two runs from stacking.

The service PATH is `~/.local/bin:/usr/local/bin:/usr/bin:/bin`. It does
not load mise. On this Pi, uv is installed by mise, so the oneshot will
not see it unless `UV=` in `~/.config/kingfisher/jpi.env` is an absolute
path. From a login shell, `command -v uv` prints that path (the mise shim
is `~/.local/share/mise/shims/uv`). When `UV` is unset, the script uses
`command -v uv` and exits if that finds nothing.

Thresholds Eric tunes live in `~/kingfisher/engine/limits.json`. See
`docs/analysis/engine.md`.
