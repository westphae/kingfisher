#!/bin/sh
# Kingfisher flight-data backup: rsync ~/kingfisher/flights -> NAS.
#
# Push-only, additive (never --delete: a backup must not follow local cleanup).
# Runs on the wlan1-up NM dispatcher event and on an hourly fallback timer, so it
# fires whenever the Pi is home on the LAN and is a no-op otherwise. Safe to run
# while kingfisher is recording: the live DB and all -wal/-shm sidecars are
# excluded, so only closed, consistent flight DBs are copied.
#
# Transport is chosen in the config (TRANSPORT=rsyncd|ssh).
#
# Install: sudo install -m 755 deploy/backup/kingfisher-flight-backup.sh /usr/local/bin/
# Config:  /etc/kingfisher-backup.conf (see kingfisher-backup.conf.example).

set -eu

CONF="${KINGFISHER_BACKUP_CONF:-/etc/kingfisher-backup.conf}"

# Defaults (overridden by the conf file).
SRC_DIR="/home/eric/kingfisher/flights"
DEST_HOST="192.168.86.40"
REQUIRE_IFACE="wlan1"
TRANSPORT="rsyncd"
RSYNC_USER="kingfisher"
RSYNC_MODULE="kingfisher"
RSYNC_MODULE_PATH="flights"
RSYNC_SECRET="/home/eric/.config/kingfisher/backup.secret"
SSH_USER="admin"
SSH_PORT="22"
SSH_KEY="/home/eric/.ssh/kingfisher_backup"
SSH_PATH="/volume1/Public/data/kingfisher/flights"

# shellcheck source=/dev/null
[ -r "$CONF" ] && . "$CONF"

log() { echo "kingfisher-backup: $*"; }
die() { log "$*"; exit 1; }

# --- single-instance lock (a slow transfer must not overlap the next trigger) ---
LOCK="/tmp/kingfisher-backup.lock"
if command -v flock >/dev/null 2>&1; then
	exec 9>"$LOCK" || die "cannot open lock $LOCK"
	flock -n 9 || { log "another backup is already running; skipping"; exit 0; }
fi

[ -d "$SRC_DIR" ] || die "source dir $SRC_DIR missing"

# --- resolve transport into DEST + rsync transport args ($XPORT is -e/--password-file) ---
PROBE_PORT=""
DEST=""
XPORT=""
XVAL=""
case "$TRANSPORT" in
rsyncd)
	# rsync itself refuses a --password-file that is group/other-accessible;
	# keep it chmod 600 (see verify.md).
	[ -r "$RSYNC_SECRET" ] || die "rsync secret $RSYNC_SECRET unreadable"
	PROBE_PORT="873"
	DEST="rsync://${RSYNC_USER}@${DEST_HOST}/${RSYNC_MODULE}/${RSYNC_MODULE_PATH}/"
	XPORT="--password-file"
	XVAL="$RSYNC_SECRET"
	;;
ssh)
	[ -r "$SSH_KEY" ] || die "ssh key $SSH_KEY unreadable"
	PROBE_PORT="$SSH_PORT"
	DEST="${SSH_USER}@${DEST_HOST}:${SSH_PATH}/"
	XPORT="-e"
	XVAL="ssh -i $SSH_KEY -p $SSH_PORT -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
	;;
*)
	die "unknown TRANSPORT '$TRANSPORT' (want rsyncd or ssh)"
	;;
esac

# --- only when the home-LAN iface actually has an address ---
if [ -n "$REQUIRE_IFACE" ]; then
	if ! ip -4 addr show dev "$REQUIRE_IFACE" scope global 2>/dev/null | grep -q 'inet '; then
		log "$REQUIRE_IFACE has no global address; not home — skipping"
		exit 0
	fi
fi

# --- reachability probe (fail fast instead of a long transport hang each cycle) ---
if command -v nc >/dev/null 2>&1; then
	nc -z -w4 "$DEST_HOST" "$PROBE_PORT" 2>/dev/null || { log "$DEST_HOST:$PROBE_PORT unreachable; skipping"; exit 0; }
else
	timeout 4 sh -c "echo > /dev/tcp/$DEST_HOST/$PROBE_PORT" 2>/dev/null || { log "$DEST_HOST:$PROBE_PORT unreachable; skipping"; exit 0; }
fi

# --- never copy the DB kingfisher currently holds open (torn read) ---
# Read the recorder's open fds straight from /proc (no lsof dependency).
ACTIVE=""
PID="$(pgrep -x kingfisher 2>/dev/null | head -1 || true)"
if [ -n "$PID" ] && [ -d "/proc/$PID/fd" ]; then
	ACTIVE="$(readlink /proc/"$PID"/fd/* 2>/dev/null \
		| grep "^${SRC_DIR}/.*\.db\$" | head -1 | xargs -r basename 2>/dev/null || true)"
fi
if [ -z "$ACTIVE" ]; then
	# Fallback when kingfisher isn't running (nothing open): the newest .db is
	# the likely-active one. Harmless to skip if it's actually closed — the next
	# run picks it up.
	ACTIVE="$(ls -t "$SRC_DIR"/*.db 2>/dev/null | head -1 | xargs -r basename 2>/dev/null || true)"
fi

set -- -a --partial --partial-dir=.kf-partial \
	--exclude='*-wal' --exclude='*-shm' --exclude='.kf-partial' \
	"$XPORT" "$XVAL"
[ -n "$ACTIVE" ] && { set -- "$@" --exclude="/$ACTIVE"; log "excluding active DB $ACTIVE"; }
set -- "$@" "$SRC_DIR/" "$DEST"

log "rsync ($TRANSPORT) -> $DEST"
if rsync "$@"; then
	log "backup complete"
else
	rc=$?
	die "rsync failed (exit $rc)"
fi

# Record what the NAS now holds so the cockpit Flights page can show per-flight
# backup state even when the NAS is unreachable (i.e. in the aircraft). Format:
# "<name>\t<bytes>" per .db. Written atomically; file mtime = manifest age.
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/kingfisher"
mkdir -p "$STATE_DIR"
if listing=$(rsync "$XPORT" "$XVAL" "$DEST" 2>/dev/null); then
	printf '%s\n' "$listing" \
		| awk '$NF ~ /\.db$/ { size=$2; gsub(",","",size); print $NF "\t" size }' \
		> "$STATE_DIR/backup-manifest.tsv.tmp" \
		&& mv "$STATE_DIR/backup-manifest.tsv.tmp" "$STATE_DIR/backup-manifest.tsv"
	log "manifest updated ($(wc -l < "$STATE_DIR/backup-manifest.tsv") entries)"
else
	log "manifest listing failed (backup itself succeeded)"
fi
