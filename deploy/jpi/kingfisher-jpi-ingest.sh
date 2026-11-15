#!/bin/sh
# Pull new EDM-930 downloads from the NAS, merge them into local flight DBs,
# and refresh the engine review cache.
#
# The NAS is read-only from this script's point of view. The NAS account is
# forced to `rrsync -ro` rooted at the JPI folder, so the rsync source is
# user@host: with no path. rsync copies U*.JPI into ~/kingfisher/engine/raw
# and never uses --delete. Flight databases are not fetched. Merges are
# written only to the Pi's own SQLite files.
#
# The key is named by path in ~/.config/kingfisher/jpi.env (outside this
# repo). This script never prints or copies the key.
#
# If the NAS is down, the key is unreadable, or TRANSPORT=none, the sync is
# skipped and the local ingest still runs. A flight that fails the alignment
# gates is recorded in the ledger and is not a unit failure.
#
# Install: see deploy/jpi/verify.md.

set -eu

ENV_FILE="${KINGFISHER_JPI_ENV:-${HOME}/.config/kingfisher/jpi.env}"
CONF="${KINGFISHER_JPI_CONF:-/etc/kingfisher-jpi-ingest.conf}"

RAW_DIR="${HOME}/kingfisher/engine/raw"
FLIGHTS_DIR="${HOME}/kingfisher/flights"
LEDGER="${HOME}/kingfisher/engine/ingest.json"
REPO="${HOME}/go/src/github.com/westphae/kingfisher"
# UV is resolved after jpi.env is sourced. systemd does not load mise, so
# an absolute path in jpi.env is how the oneshot finds uv on the Pi.

# none | ssh | rsyncd. none is the default so a checkout without jpi.env
# does not try the NAS. jpi.env.example turns ssh on.
TRANSPORT="none"
REQUIRE_IFACE="wlan1"
NAS_HOST=""
RSYNC_USER="kingfisher"
RSYNC_MODULE="bonanza"
RSYNC_MODULE_PATH="JPI"
RSYNC_SECRET="${HOME}/.config/kingfisher/jpi.secret"
SSH_USER="eric"
SSH_PORT="22"
# Restricted key on the Pi. Never a path inside the git checkout.
SSH_KEY="${HOME}/.ssh/nas_ro"
# Empty when rrsync -ro is rooted at the JPI folder.
NAS_PATH=""

# shellcheck source=/dev/null
[ -r "$ENV_FILE" ] && . "$ENV_FILE"
# shellcheck source=/dev/null
[ -r "$CONF" ] && . "$CONF"

# Inherited UV= wins over the default, and jpi.env (sourced above) wins
# over both. Empty means "look it up".
if [ -z "${UV:-}" ]; then
	UV="$(command -v uv 2>/dev/null || true)"
fi

log() { echo "kingfisher-jpi: $*"; }
die() { log "$*"; exit 1; }

[ -n "$UV" ] || die "uv not found; set UV= in $ENV_FILE (mise is not on the oneshot PATH)"

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
		# rrsync -ro is already rooted at the JPI folder, so the source is
		# user@host: and not a volume path. Flight DBs are not part of this pull.
		if [ -z "$NAS_PATH" ]; then
			src="${SSH_USER}@${NAS_HOST}:"
		else
			src="${SSH_USER}@${NAS_HOST}:${NAS_PATH}/"
		fi
		xport="-e"
		ssh_e="ssh -i ${SSH_KEY} -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10"
		if [ "$SSH_PORT" != "22" ]; then
			ssh_e="ssh -i ${SSH_KEY} -p ${SSH_PORT} -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10"
		fi
		xval="$ssh_e"
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
	# Read-only toward the NAS. No --delete. Only whole-download names at
	# the rrsync root. FLIGHTS_DIR is local and is not an rsync source.
	if ! rsync -a --partial \
		--include='U*.JPI' --exclude='*' \
		"$xport" "$xval" "$src" "$RAW_DIR/"; then
		log "rsync failed; continuing with files already in $RAW_DIR"
	fi
}

sync_nas

[ -d "$FLIGHTS_DIR" ] || die "flights dir $FLIGHTS_DIR missing"
[ -d "$REPO" ] || die "repo $REPO missing"

cd "$REPO"
log "jpi-auto"
# --flights-dir is a top-level option. It has to come before the subcommand.
# The subcommands also accept it, so a copy of this script that still passes
# it afterwards does not die with "unrecognized arguments".
"$UV" run --project analysis python scripts/analyze_flights.py \
	--flights-dir "$FLIGHTS_DIR" \
	jpi-auto \
	--jpi-dir "$RAW_DIR" \
	--ledger "$LEDGER"
log "engine review"
"$UV" run --project analysis python scripts/analyze_flights.py \
	--flights-dir "$FLIGHTS_DIR" \
	engine \
	--all \
	--jpi-dir "$RAW_DIR"
log "done"
