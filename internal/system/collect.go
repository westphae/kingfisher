// Package system publishes a `system` virtual device carrying Raspberry Pi
// host telemetry: supply voltage, throttle/undervoltage flags, CPU
// temperature/load/frequency, memory, disk, and cooling. It follows the
// same hub → flight-DB path as every sensor source (see internal/live).
//
// Collectors read /proc and /sys (fork-free); the supply voltage and the
// sticky throttle bits come from vcgencmd (see vcgencmd.go). Parsing is
// split into pure functions so it can be unit-tested off-hardware; the
// glue that reads real paths is exercised live on the Pi.
package system

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// Device is the hub/flight-DB name for the host telemetry stream.
const Device = "system"

// readTrim reads a small sysfs/procfs file and trims trailing whitespace.
func readTrim(path string) (string, bool) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	return strings.TrimSpace(string(b)), true
}

// parseMilliC converts a millidegree/millivolt integer string to a float in
// base units (e.g. "51250" -> 51.25).
func parseMilliC(s string) (float64, bool) {
	v, err := strconv.ParseInt(strings.TrimSpace(s), 10, 64)
	if err != nil {
		return 0, false
	}
	return float64(v) / 1000, true
}

// parseLoadAvg extracts the 1/5/15-minute load averages from /proc/loadavg.
func parseLoadAvg(s string) (l1, l5, l15 float64, ok bool) {
	f := strings.Fields(s)
	if len(f) < 3 {
		return 0, 0, 0, false
	}
	var err error
	if l1, err = strconv.ParseFloat(f[0], 64); err != nil {
		return 0, 0, 0, false
	}
	if l5, err = strconv.ParseFloat(f[1], 64); err != nil {
		return 0, 0, 0, false
	}
	if l15, err = strconv.ParseFloat(f[2], 64); err != nil {
		return 0, 0, 0, false
	}
	return l1, l5, l15, true
}

// parseUptime extracts the uptime (seconds) from /proc/uptime.
func parseUptime(s string) (float64, bool) {
	f := strings.Fields(s)
	if len(f) < 1 {
		return 0, false
	}
	v, err := strconv.ParseFloat(f[0], 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

// parseMeminfo returns selected /proc/meminfo fields in kB, keyed by name
// without the trailing colon (MemTotal, MemAvailable, SwapTotal, SwapFree).
func parseMeminfo(s string) map[string]int64 {
	out := make(map[string]int64, 8)
	for _, line := range strings.Split(s, "\n") {
		f := strings.Fields(line)
		if len(f) < 2 {
			continue
		}
		key := strings.TrimSuffix(f[0], ":")
		v, err := strconv.ParseInt(f[1], 10, 64)
		if err != nil {
			continue
		}
		out[key] = v
	}
	return out
}

// parseCPUStat returns the idle and total jiffy counters from the aggregate
// "cpu " line of /proc/stat. idle includes iowait.
func parseCPUStat(s string) (idle, total int64, ok bool) {
	for _, line := range strings.Split(s, "\n") {
		if !strings.HasPrefix(line, "cpu ") {
			continue
		}
		f := strings.Fields(line)[1:] // user nice system idle iowait irq softirq steal ...
		if len(f) < 5 {
			return 0, 0, false
		}
		vals := make([]int64, 0, len(f))
		for _, tok := range f {
			v, err := strconv.ParseInt(tok, 10, 64)
			if err != nil {
				return 0, 0, false
			}
			vals = append(vals, v)
		}
		for _, v := range vals {
			total += v
		}
		idle = vals[3] + vals[4] // idle + iowait
		return idle, total, true
	}
	return 0, 0, false
}

// parseSelfRSSkB extracts VmRSS (kB) from /proc/self/status.
func parseSelfRSSkB(s string) (int64, bool) {
	for _, line := range strings.Split(s, "\n") {
		if !strings.HasPrefix(line, "VmRSS:") {
			continue
		}
		f := strings.Fields(line)
		if len(f) < 2 {
			return 0, false
		}
		v, err := strconv.ParseInt(f[1], 10, 64)
		if err != nil {
			return 0, false
		}
		return v, true
	}
	return 0, false
}

// discoverHwmon maps hwmon device names (nvme, pwmfan, rpi_volt, cpu_thermal)
// to their /sys/class/hwmon/hwmonN directory. Numbering is not stable across
// boots, so we resolve by name.
func discoverHwmon() map[string]string {
	out := make(map[string]string, 8)
	dirs, err := filepath.Glob("/sys/class/hwmon/hwmon*")
	if err != nil {
		return out
	}
	for _, d := range dirs {
		if name, ok := readTrim(filepath.Join(d, "name")); ok {
			out[name] = d
		}
	}
	return out
}

// collectProc gathers the fork-free /proc and /sys metrics into vals. cpu_pct
// needs a delta against the previous sample, so the caller threads prevIdle/
// prevTotal; the first call (havePrev=false) omits cpu_pct and seeds them.
func collectProc(vals map[string]float64, prevIdle, prevTotal int64, havePrev bool) (idle, total int64, cpuOK bool) {
	if s, ok := readTrim("/proc/loadavg"); ok {
		if l1, l5, l15, ok := parseLoadAvg(s); ok {
			vals["load_1m"] = l1
			vals["load_5m"] = l5
			vals["load_15m"] = l15
		}
	}
	if s, ok := readTrim("/proc/uptime"); ok {
		if up, ok := parseUptime(s); ok {
			vals["uptime_s"] = up
		}
	}
	if s, ok := readTrim("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"); ok {
		if khz, err := strconv.ParseInt(s, 10, 64); err == nil {
			vals["cpu_freq_mhz"] = float64(khz) / 1000
		}
	}
	if s, ok := readTrim("/proc/meminfo"); ok {
		m := parseMeminfo(s)
		if mt := m["MemTotal"]; mt > 0 {
			if ma, ok := m["MemAvailable"]; ok {
				vals["mem_used_pct"] = float64(mt-ma) / float64(mt) * 100
				vals["mem_avail_mb"] = float64(ma) / 1024
			}
		}
		if st := m["SwapTotal"]; st > 0 {
			sf := m["SwapFree"]
			vals["swap_used_mb"] = float64(st-sf) / 1024
			vals["swap_used_pct"] = float64(st-sf) / float64(st) * 100
		} else {
			vals["swap_used_mb"] = 0
		}
	}
	if s, ok := readTrim("/proc/stat"); ok {
		if i, t, ok := parseCPUStat(s); ok {
			if havePrev && t > prevTotal {
				dt := float64(t - prevTotal)
				di := float64(i - prevIdle)
				vals["cpu_pct"] = (1 - di/dt) * 100
			}
			return i, t, true
		}
	}
	return prevIdle, prevTotal, false
}

// collectHwmon reads CPU temperature (thermal_zone0), NVMe temperature, fan
// RPM, and the rpi_volt undervoltage alarm from the resolved hwmon map.
func collectHwmon(vals map[string]float64, hwmon map[string]string) {
	if s, ok := readTrim("/sys/class/thermal/thermal_zone0/temp"); ok {
		if c, ok := parseMilliC(s); ok {
			vals["cpu_temp_c"] = c
		}
	}
	if d := hwmon["nvme"]; d != "" {
		if s, ok := readTrim(filepath.Join(d, "temp1_input")); ok {
			if c, ok := parseMilliC(s); ok {
				vals["nvme_temp_c"] = c
			}
		}
	}
	if d := hwmon["pwmfan"]; d != "" {
		if s, ok := readTrim(filepath.Join(d, "fan1_input")); ok {
			if rpm, err := strconv.ParseInt(s, 10, 64); err == nil {
				vals["fan_rpm"] = float64(rpm)
			}
		}
	}
	// rpi_volt exposes a fork-free live undervoltage alarm; used as a
	// supplement to (and fallback for) vcgencmd get_throttled.
	if d := hwmon["rpi_volt"]; d != "" {
		if s, ok := readTrim(filepath.Join(d, "in0_lcrit_alarm")); ok {
			if a, err := strconv.ParseInt(s, 10, 64); err == nil {
				vals["undervolt_now"] = float64(a)
			}
		}
	}
}
