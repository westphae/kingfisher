#!/bin/bash
# Temporary diagnostic (2026-11-20, Eric approved): sample NVMe temperature
# and I/O so a freeze can be traced to the drive. Read-only; does not reboot,
# does not touch kingfisher.service, and does not change the watchdog.
#
# Install:
#   sudo install -m 755 deploy/nvme-trace/nvme-trace.sh /usr/local/bin/nvme-trace.sh
#   sudo install -m 644 deploy/nvme-trace/nvme-trace.service /etc/systemd/system/nvme-trace.service
#   sudo systemctl daemon-reload && sudo systemctl enable --now nvme-trace.service
#
# Remove:
#   sudo systemctl disable --now nvme-trace.service
#   sudo rm -f /etc/systemd/system/nvme-trace.service /usr/local/bin/nvme-trace.sh
#   sudo systemctl daemon-reload
#   # optional: sudo rm -f /var/log/nvme-trace.log*
#
# Log: /var/log/nvme-trace.log (fsync'd per sample, rotated at 32 MiB, 3 backups).

interval="${1:-1}"
TRACE="${NVME_TRACE:-/var/log/nvme-trace.log}"
MAX_BYTES="${NVME_MAX_BYTES:-33554432}"
KEEP=3
DISK="${NVME_DISK:-nvme0n1}"

find_hw() {
  local h
  for h in /sys/class/hwmon/hwmon*; do
    [ "$(cat "$h/name" 2>/dev/null)" = "nvme" ] && { echo "$h"; return; }
  done
}
HW=$(find_hw)

rotate() {
  local i
  for ((i=KEEP-1; i>=1; i--)); do
    [ -f "$TRACE.$i" ] && mv -f "$TRACE.$i" "$TRACE.$((i+1))"
  done
  [ -f "$TRACE" ] && mv -f "$TRACE" "$TRACE.1"
  : > "$TRACE"
}

[ -f "$TRACE" ] || : > "$TRACE"
chmod 0644 "$TRACE" 2>/dev/null

temps() {
  # composite (temp1) and Kingston "sensor 2" (often temp3). Missing -> NA.
  local c="NA" s="NA" t
  if [ -n "$HW" ]; then
    [ -r "$HW/temp1_input" ] && { read -r t < "$HW/temp1_input"; c=$((t/1000)); }
    if [ -r "$HW/temp3_input" ]; then
      read -r t < "$HW/temp3_input"; s=$((t/1000))
    elif [ -r "$HW/temp2_input" ]; then
      read -r t < "$HW/temp2_input"; s=$((t/1000))
    fi
  fi
  echo "$c $s"
}

disk() {
  # /proc/diskstats fields: 4 rio 6 rsect 8 wio 10 wsect 12 inflight 13 ioticks 14 time_in_queue
  local line f
  line=$(awk -v d="$DISK" '$3==d {print}' /proc/diskstats)
  if [ -z "$line" ]; then
    echo "rio=NA rsect=NA wio=NA wsect=NA inflight=NA ioticks=NA queue=NA"
    return
  fi
  set -- $line
  echo "rio=$4 rsect=$6 wio=$8 wsect=${10} inflight=${12} ioticks=${13} queue=${14}"
}

dstate() {
  # Count tasks in uninterruptible sleep (D). Cheap: one walk of /proc.
  local n=0 s
  for s in /proc/[0-9]*/stat; do
    [ -r "$s" ] || continue
    # comm may contain spaces in parentheses; state is the first field after ')'.
    case "${s##*/}" in
      *) ;;
    esac
    read -r _ rest < "$s" 2>/dev/null || continue
    rest="${rest##*)}"
    set -- $rest
    [ "$1" = "D" ] && n=$((n+1))
  done
  echo "$n"
}

psi_io() {
  # Prints: <some_avg10>,<some_total> <full_avg10>,<full_total>
  local some="NA,NA" full="NA,NA"
  if [ -r /proc/pressure/io ]; then
    some=$(awk '/^some/ {printf "%s,%s", substr($2,7), substr($5,7)}' /proc/pressure/io)
    full=$(awk '/^full/ {printf "%s,%s", substr($2,7), substr($5,7)}' /proc/pressure/io)
    [ -n "$some" ] || some="NA,NA"
    [ -n "$full" ] || full="NA,NA"
  fi
  echo "$some $full"
}

while :; do
  [ -d /sys/class/hwmon ] || { sleep "$interval"; continue; }
  [ -n "$HW" ] || HW=$(find_hw)
  set -- $(temps)
  ct=$1; st=$2
  ds=$(disk)
  nd=$(dstate)
  set -- $(psi_io)
  some=$1; full=$2
  printf '%s tcomp=%s tsens2=%s %s dstate=%s psi_some=%s psi_full=%s\n' \
    "$EPOCHREALTIME" "$ct" "$st" "$ds" "$nd" "$some" "$full" >> "$TRACE"
  sync -d "$TRACE" 2>/dev/null || true
  if [ $((RANDOM % 128)) -eq 0 ]; then
    sz=$(stat -c %s "$TRACE" 2>/dev/null || echo 0)
    [ "$sz" -gt "$MAX_BYTES" ] && rotate
  fi
  sleep "$interval"
done
