#!/bin/sh
# Restart chrony and gpsd in the order required for SOCK refclocks.
# Optional --fix-offset adjusts the GPS refclock offset from chronyc sourcestats
# before restart (corrects stale falseticker when PPS is good but GPS serial lag
# drifted from the configured offset).
#
# Install: sudo install -m 755 deploy/time-sync/kingfisher-resync-time.sh /usr/local/bin/
# Sudoers: eric ALL=(root) NOPASSWD: /usr/local/bin/kingfisher-resync-time.sh, /usr/bin/chronyc
set -e

# Print chronyc sourcestats Offset column ($7) as signed milliseconds.
read_gps_offset_ms() {
	chronyc sourcestats 2>/dev/null | awk '/^GPS / {
		v = $7
		if (v ~ /ms$/) { sub(/ms$/, "", v); printf "%s", v; exit }
		if (v ~ /us$/) { sub(/us$/, "", v); printf "%.6f", v / 1000; exit }
		if (v ~ /ns$/) { sub(/ns$/, "", v); printf "%.6f", v / 1000000; exit }
		if (v ~ /s$/)  { sub(/s$/, "", v);  printf "%.3f", v * 1000; exit }
		exit 1
	}'
}

fix_gps_offset() {
	chrony_conf="/etc/chrony/chrony.conf"
	if [ ! -f "$chrony_conf" ] && [ -f /etc/chrony.conf ]; then
		chrony_conf="/etc/chrony.conf"
	fi
	[ -f "$chrony_conf" ] || return 0

	resid_ms="$(read_gps_offset_ms)" || return 0
	[ -n "$resid_ms" ] || return 0

	cur="$(grep -E 'refclock SOCK.*refid GPS' "$chrony_conf" | sed -n 's/.*offset \([-0-9.]*\).*/\1/p' | head -1)"
	[ -n "$cur" ] || return 0

	new="$(awk -v c="$cur" -v r="$resid_ms" 'BEGIN{printf "%.3f", c + r/1000}')"
	sed -i -E "/refclock SOCK.*refid GPS/ s/offset [-0-9.]+/offset ${new}/" "$chrony_conf"
	echo "kingfisher-resync: GPS refclock offset ${cur} -> ${new} (residual ${resid_ms}ms)"
}

case "$1" in
--fix-offset)
	fix_gps_offset
	;;
esac

systemctl restart chronyd
sleep 2
systemctl restart gpsd
sleep 5
chronyc reselect >/dev/null 2>&1 || true
