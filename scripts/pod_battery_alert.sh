#!/usr/bin/env bash
# Poll kingfisher /api/status for pod battery voltage and alert at threshold.
#
# One-shot check:
#   STATUS_URL=http://192.168.10.1:8080/api/status ./scripts/pod_battery_alert.sh --test
#
# Usage (Mac, kingfisher on Pi at 192.168.10.1):
#   STATUS_URL=http://192.168.10.1:8080/api/status ./scripts/pod_battery_alert.sh
#
# Usage (Pi, local kingfisher):
#   ./scripts/pod_battery_alert.sh
#
# Environment:
#   STATUS_URL     default: http://127.0.0.1:8080/api/status (set Pi IP on Mac)
#   THRESHOLD_V    default: 3.40
#   RECOVER_V      default: 3.45  (re-arm alert after voltage rises)
#   INTERVAL_S     default: 5
#   VOICE=1|0      default: 1
#   NOTIFY=1|0     default: 1
#   HEARTBEAT=1|0  default: 1 (log each sample to stderr; set 0 for quiet daemon)
#
# Dependencies:
#   macOS — python3, say, osascript (all built-in). Optional: brew install terminal-notifier
#   Linux — python3. Voice: espeak-ng. Notify: libnotify-bin + DBUS session
#
# Long-running:
#   macOS: scripts/pod-battery-alert.launchd.plist.example
#   Linux: scripts/pod-battery-alert.service.example

set -euo pipefail

UNAME=$(uname -s)
STATUS_URL="${STATUS_URL:-http://127.0.0.1:8080/api/status}"
THRESHOLD_V="${THRESHOLD_V:-3.40}"
RECOVER_V="${RECOVER_V:-3.45}"
INTERVAL_S="${INTERVAL_S:-5}"
if [[ "$UNAME" == "Darwin" ]]; then
  STATE_FILE="${STATE_FILE:-$HOME/Library/Caches/kingfisher-battery-alert.state}"
else
  STATE_FILE="${STATE_FILE:-${XDG_RUNTIME_DIR:-/tmp}/kingfisher-battery-alert.state}"
fi
VOICE="${VOICE:-1}"
NOTIFY="${NOTIFY:-1}"
HEARTBEAT="${HEARTBEAT:-1}"
LOG_TAG="${LOG_TAG:-kingfisher-battery-alert}"

latch=0

log() {
  local line
  line="$(date -u +"%Y-%m-%dT%H:%M:%SZ") $LOG_TAG: $*"
  # Always stderr so Terminal shows output (macOS logger succeeds but is silent here).
  echo "$line" >&2
  logger -t "$LOG_TAG" "$*" 2>/dev/null || true
}

# Returns: low | mid | recover  (string compare; safe under set -e)
voltage_band() {
  awk -v v="$1" -v t="$THRESHOLD_V" -v r="$RECOVER_V" 'BEGIN {
    if (v <= t) print "low"
    else if (v >= r) print "recover"
    else print "mid"
  }'
}

fetch_battery_v() {
  python3 - "$STATUS_URL" <<'PY'
import json, sys, urllib.request
url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=4) as r:
        d = json.load(r)
except Exception:
    sys.exit(2)
pod = d.get("pod") or {}
if not pod.get("enabled"):
    sys.exit(3)
v = None
if pod.get("has_battery_telemetry") or pod.get("has_battery"):
    v = pod.get("battery_v")
if v is None or float(v) <= 0.01:
    sys.exit(4)
print(f"{float(v):.3f}")
PY
}

# Escape a string for use inside AppleScript double quotes.
applescript_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

speak() {
  local msg="$1"
  if [[ "$VOICE" != "1" ]]; then
    return 0
  fi
  if [[ "$UNAME" == "Darwin" ]] && command -v say >/dev/null 2>&1; then
    say -r 175 "$msg" 2>/dev/null &
    return 0
  fi
  if command -v spd-say >/dev/null 2>&1; then
    spd-say -w "$msg" 2>/dev/null && return 0
  fi
  if command -v espeak-ng >/dev/null 2>&1; then
    espeak-ng -s 150 -a 200 "$msg" 2>/dev/null && return 0
  fi
  if command -v espeak >/dev/null 2>&1; then
    espeak -s 150 -a 200 "$msg" 2>/dev/null && return 0
  fi
  log "voice unavailable; message: $msg"
  return 1
}

notify_desktop() {
  local title="$1" body="$2"
  if [[ "$NOTIFY" != "1" ]]; then
    return 0
  fi
  if [[ "$UNAME" == "Darwin" ]]; then
    local t b
    t=$(applescript_escape "$title")
    b=$(applescript_escape "$body")
    if /usr/bin/osascript -e "display notification \"$b\" with title \"$t\" sound name \"Basso\"" 2>/dev/null; then
      return 0
    fi
    if command -v terminal-notifier >/dev/null 2>&1; then
      terminal-notifier -title "$title" -message "$body" -sound Basso 2>/dev/null && return 0
    fi
    return 1
  fi
  if command -v notify-send >/dev/null 2>&1 && [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
    notify-send -u critical -t 0 "$title" "$body" 2>/dev/null && return 0
  fi
  return 1
}

alert_low() {
  local v="$1"
  local msg="Pod battery ${v} volts. Below ${THRESHOLD_V}."
  log "ALERT $msg"
  speak "$msg"
  notify_desktop "Kingfisher pod battery" "$msg" || true
  printf '\a' 2>/dev/null || true
  if [[ "$UNAME" != "Darwin" ]]; then
    echo "$msg" | wall 2>/dev/null || true
  fi
}

load_latch() {
  latch=0
  if [[ -f "$STATE_FILE" ]]; then
    read -r latch <"$STATE_FILE" 2>/dev/null || latch=0
  fi
  case "$latch" in
    0 | 1) ;;
    *) latch=0 ;;
  esac
}

save_latch() {
  mkdir -p "$(dirname "$STATE_FILE")"
  printf '%s\n' "$latch" >"$STATE_FILE"
}

if [[ "$UNAME" == "Darwin" && "$STATUS_URL" == "http://127.0.0.1:8080/api/status" ]]; then
  log "hint: on Mac set STATUS_URL to your Pi, e.g. http://192.168.10.1:8080/api/status"
fi

if [[ "${1:-}" == "--test" ]]; then
  if v=$(fetch_battery_v); then
    echo "battery_v=${v}V band=$(voltage_band "$v") latch_file=$STATE_FILE"
    exit 0
  fi
  echo "failed to read battery from $STATUS_URL" >&2
  exit 1
fi

log "monitoring $STATUS_URL threshold=${THRESHOLD_V}V recover=${RECOVER_V}V interval=${INTERVAL_S}s ($UNAME)"
load_latch
save_latch
log "state file $STATE_FILE latch=$latch (0=armed, 1=already alerted until recover)"

while true; do
  v=""
  if v=$(fetch_battery_v 2>/dev/null); then
    band=$(voltage_band "$v" | tr -d '[:space:]')
    if [[ "$HEARTBEAT" == "1" ]]; then
      log "${v}V band=${band} latch=${latch}"
    fi
    case "$band" in
      low)
        if [[ "$latch" == "0" ]]; then
          alert_low "$v"
          latch=1
          save_latch
        fi
        ;;
      recover)
        if [[ "$latch" == "1" ]]; then
          log "recovered ${v}V (re-armed)"
          latch=0
          save_latch
        fi
        ;;
      mid)
        :
        ;;
      *)
        log "unexpected band='${band}' at ${v}V"
        ;;
    esac
  else
    log "no battery reading (kingfisher unreachable or pod offline?)"
  fi
  sleep "$INTERVAL_S"
done
