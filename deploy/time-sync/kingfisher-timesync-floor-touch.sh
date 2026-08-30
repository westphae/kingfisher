#!/bin/bash
# kingfisher-timesync-floor-touch — advances systemd's clock-floor file.
#
# systemd refuses to step the wall clock backward past the newest mtime among a
# few reference files, one of which is /var/lib/systemd/timesync/clock — normally
# systemd-timesyncd touches that file itself while it runs. This box disciplines
# the clock with chrony instead (see docs/time-sync.md), so nothing ever advances
# the floor: it stays pinned at whenever the file was last written (image build,
# or the last manual touch), regardless of how much real time has passed.
#
# That matters because the RTC here does not reliably hold time across a power
# cycle (see deploy/time-sync/kingfisher-clock-check.sh and the rtc-battery
# notes): on a cold boot the kernel often sets the clock from an unpowered RTC
# read (1970), and systemd clamps that forward to the stale floor instead of
# 1970 — so the ~15-20s window before GPS/PPS lock reads whatever the floor
# says, which without this script can be weeks wrong instead of minutes wrong.
# Anything timestamped in that window (e.g. a fallback-named flight DB's
# _session.start_time) inherits the error.
#
# Only touches the file once chrony is actually GPS/PPS-disciplined, so the
# floor only ever advances to a time already known to be good.
set -uo pipefail

CLOCK_FILE=/var/lib/systemd/timesync/clock
CHRONYC=/usr/bin/chronyc

log() { echo "kingfisher-timesync-floor-touch: $*"; }

ref=$($CHRONYC tracking 2>/dev/null | sed -n 's/^Reference ID *: *//p')
if ! printf '%s' "$ref" | grep -qE '\((PPS|GPS)\)'; then
	log "not GPS/PPS-synced (Reference ID: ${ref:-none}); skipping"
	exit 0
fi

if touch "$CLOCK_FILE"; then
	log "floor advanced to $(date -u +%FT%TZ) (synced to $ref)"
else
	log "touch $CLOCK_FILE failed"
	exit 1
fi
