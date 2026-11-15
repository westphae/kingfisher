#!/bin/sh
# Pull new EDM-930 downloads from the NAS, merge them into local flight DBs,
# and refresh the engine review cache.
#
# The NAS is read-only from this script's point of view: rsync copies U*.JPI
# into ~/kingfisher/engine/raw and never --delete. Merges are written only to
# the Pi's own flight SQLite files, never to a copy that lives on the NAS.
#
# If the NAS is down, the secret is missing, or TRANSPORT=none, the sync is
# skipped and the local ingest still runs. A flight that fails the alignment
# gates is recorded in the ledger and is not a unit failure.
#
# Install: see deploy/jpi/verify.md.

set -eu

CONF="${KINGFISHER_JPI_CONF:-/etc/kingfisher-jpi-ingest.conf}"

RAW_DIR="/home/eric/kingfisher/engine/raw"
FLIGHTS_DIR="/home/eric/kingfisher/flights"
LEDGER="/home/eric/kingfisher/engine/ingest.json"
REPO="/home/eric/go/src/github.com/westphae/kingfisher"
UV="${UV:-uv}"

# none | rsyncd | ssh. none is the default so a fresh install does not
# fail looking for a share that has not been exported yet.
TRANSPORT="none"
REQUIRE_IFACE="wlan1"
NAS_HOST=""
RSYNC_USER="kingfisher"
RSYNC_MODULE="bonanza"
RSYNC_MODULE_PATH="JPI"
RSYNC_SECRET="/home/eric/.config/kingfisher/jpi.secret"
SSH_USER="eric"
SSH_PORT="22"
SSH_KEY="/home/eric/.ssh/kingfisher_jpi"
# The path Eric named. The flight-backup host in deploy/backup is an Asustor;
# this archive may be a different NAS. Leave NAS_HOST empty until that is confirmed.
NAS_PATH="/volume1/Public/archive/Documents/Bonanza/JPI"

# shellcheck source=/dev/null
[ -r "$CONF" ] && . "$CONF"

log() { echo "kingfisher-jpi: $*"; }
die() { log "$*"; exit 1; }

LOCK="/tmp/kingfisher-jpi-ingest.lock"
if command -v flock >/dev/null 2>&1; then
	exec 9>"$LOCK" || die "cannot open lock $LOCK"
	flock -n 9 || { log "another ingest is already running; skipping"; exit 0; }
fi

mkdir -p "$RAW_DIR"

sync_nas() {
	if [ "$TRANSPORT" = "none" ] || [ -z "$NAS_HOST" ]; then
		log "NAS sync off (TRANSPORT=$TRANSPORT, NAS_HOST=${NAS_HOST:-empty})"
		return 0
	fi
	if [ -n "$REQUIRE_IFACE" ]; then
		if ! ip -4 addr show dev "$REQUIRE_IFACE" scope global 2>/dev/null | grep -q 'inet '; then
			log "$REQUIRE_IFACE has no address; not pulling from the NAS"
			return 0
		fi
	fi
	case "$TRANSPORT" in
	rsyncd)
		[ -r "$RSYNC_SECRET" ] || { log "rsync secret $RSYNC_SECRET unreadable; skipping NAS"; return 0; }
		probe=873
		src="rsync://${RSYNC_USER}@${NAS_HOST}/${RSYNC_MODULE}/${RSYNC_MODULE_PATH}/"
		xport="--password-file"
		xval="$RSYNC_SECRET"
		;;
	ssh)
		[ -r "$SSH_KEY" ] || { log "ssh key $SSH_KEY unreadable; skipping NAS"; return 0; }
		probe="$SSH_PORT"
		src="${SSH_USER}@${NAS_HOST}:${NAS_PATH}/"
		xport="-e"
		xval="ssh -i $SSH_KEY -p $SSH_PORT -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
		;;
	*)
		log "unknown TRANSPORT '$TRANSPORT'; skipping NAS"
		return 0
		;;
	esac
	if command -v nc >/dev/null 2>&1; then
		nc -z -w4 "$NAS_HOST" "$probe" 2>/dev/null || { log "$NAS_HOST:$probe unreachable; skipping NAS"; return 0; }
	else
		timeout 4 sh -c "echo > /dev/tcp/$NAS_HOST/$probe" 2>/dev/null || { log "$NAS_HOST:$probe unreachable; skipping NAS"; return 0; }
	fi
	log "rsync ($TRANSPORT) $src -> $RAW_DIR"
	# Read-only toward the NAS. No --delete: a renamed download stays until
	# Eric removes it. Only the whole-download names.
	if ! rsync -a --partial \
		--include='U*.JPI' --include='U*.jpi' --include='U*.DAT' --include='U*.dat' \
		--exclude='*' \
		"$xport" "$xval" "$src" "$RAW_DIR/"; then
		log "rsync failed; continuing with files already in $RAW_DIR"
	fi
}

sync_nas

[ -d "$FLIGHTS_DIR" ] || die "flights dir $FLIGHTS_DIR missing"
[ -d "$REPO" ] || die "repo $REPO missing"

cd "$REPO"
log "jpi-auto"
"$UV" run --project analysis python scripts/analyze_flights.py jpi-auto \
	--flights-dir "$FLIGHTS_DIR" \
	--jpi-dir "$RAW_DIR" \
	--ledger "$LEDGER"
log "engine review"
"$UV" run --project analysis python scripts/analyze_flights.py engine \
	--all \
	--flights-dir "$FLIGHTS_DIR" \
	--jpi-dir "$RAW_DIR"
log "done"
