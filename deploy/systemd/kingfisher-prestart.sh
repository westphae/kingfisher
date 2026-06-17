#!/bin/bash
# Wait for chrony before kingfisher opens a flight DB.
# - Synced (PPS/GPS locked): return in milliseconds.
# - Cold boot / unsynced: return as soon as chrony locks; bounded ceiling so the
#   start is always prompt (chrony now PPS-locks in ~15-40 s — see chrony.conf).
# - Always exit 0 so kingfisher starts even if chrony never locks (RTC battery
#   keeps the clock close; chrony disciplines it once GNSS comes up).
set -euo pipefail

CHRONYC=/usr/bin/chronyc
MAX_CORR=0.1
POLL=2                 # waitsync poll interval (s)
BOOT_WAIT_TRIES=60     # cold boot: up to ~120 s, but returns immediately on lock
RESTART_WAIT_TRIES=15  # restart unsynced: up to ~30 s

synced() {
	$CHRONYC tracking 2>/dev/null | grep -qE 'Reference ID\s+:\s+[0-9A-Fa-f]{8} \((PPS|GPS)\)'
}

uptime_s() {
	cut -d. -f1 /proc/uptime
}

if synced; then
	$CHRONYC waitsync 3 "$MAX_CORR" 0.0 "$POLL" || true
	exit 0
fi

if [ "$(uptime_s)" -lt 300 ]; then
	$CHRONYC waitsync "$BOOT_WAIT_TRIES" "$MAX_CORR" 0.0 "$POLL" || true
else
	$CHRONYC waitsync "$RESTART_WAIT_TRIES" "$MAX_CORR" 0.0 "$POLL" || true
fi
exit 0
