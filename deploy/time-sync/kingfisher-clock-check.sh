#!/bin/bash
# kingfisher-clock-check — boot-time clock sanity check.
# Logs to the journal only; NEVER changes the clock.
#
# When chrony is PPS-disciplined the system clock is correct to sub-microsecond,
# so the one realistic failure mode is an INTEGER-SECOND error: a PPS pulse locked
# to the wrong second. That failure is internally consistent (PPS + system agree),
# so we cross-check against the GPS NMEA source, whose UTC comes straight from the
# satellite navigation message and is therefore independent of the PPS second-
# numbering. Its chrony "offset" is normally just the serial latency (~0..0.65 s on
# this unit, and it varies per boot); a whole-second mislock shifts it by ~±1 s,
# pushing it out of the band below.
set -uo pipefail

CHRONYC=/usr/bin/chronyc
LO=-0.2     # GPS offset below this => system clock likely ~1 s SLOW
HI=0.9      # GPS offset above this => system clock likely ~1 s FAST
            # (band absorbs the variable NMEA serial latency, ~0..0.65 s)

log() { echo "kingfisher-clock-check: $*"; }

# Give chrony a brief, bounded chance to lock. Nothing depends on this unit, so
# this can never delay boot or kingfisher.
$CHRONYC waitsync 30 0.1 0.0 1 >/dev/null 2>&1 || true

ref=$($CHRONYC tracking 2>/dev/null | sed -n 's/^Reference ID *: *//p')
sysoff=$($CHRONYC tracking 2>/dev/null | sed -n 's/^System time *: *//p')

if ! printf '%s' "$ref" | grep -qE '\((PPS|GPS)\)'; then
	log "WARN: chrony NOT synced to GPS/PPS (Reference ID: ${ref:-none}); System time: ${sysoff:-?}"
	exit 0
fi

# GPS NMEA source offset = CSV field 8; our independent wrong-second probe.
goff=$($CHRONYC -c sources 2>/dev/null | awk -F, '$3=="GPS"{print $8; exit}')

if [ -z "${goff:-}" ]; then
	log "OK(partial): synced ($ref); System time ${sysoff}; GPS NMEA source absent — wrong-second cross-check skipped"
	exit 0
fi

if awk -v g="$goff" -v lo="$LO" -v hi="$HI" 'BEGIN{exit !(g<lo || g>hi)}'; then
	log "WARN: possible WRONG-SECOND clock error! GPS cross-check offset ${goff}s is outside [${LO},${HI}]s (expected ~serial latency). Synced to $ref; System time ${sysoff}"
else
	log "OK: synced to $ref; System time ${sysoff}; GPS cross-check offset ${goff}s within latency band — second-numbering looks correct"
fi
exit 0
