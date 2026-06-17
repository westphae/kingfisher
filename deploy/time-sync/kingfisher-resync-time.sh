#!/bin/sh
# Restart chrony and gpsd in the order required for SOCK refclocks: chrony first
# (so it recreates the SOCKets), then gpsd (so it reconnects). Restarting chrony
# alone leaves gpsd writing to stale sockets -> GPS/PPS go #? with Reach 0.
#
# Ordered restart only. (There is deliberately no GPS-offset auto-correction: the
# serial source is `noselect` and its latency varies per boot, so chasing the
# offset is counterproductive — it was the original cause of falseticker lockups.)
#
# Install: sudo install -m 755 deploy/time-sync/kingfisher-resync-time.sh /usr/local/bin/
# Sudoers: eric ALL=(root) NOPASSWD: /usr/local/bin/kingfisher-resync-time.sh, /usr/bin/chronyc
set -e

systemctl restart chronyd
sleep 2
systemctl restart gpsd
sleep 5
chronyc reselect >/dev/null 2>&1 || true
