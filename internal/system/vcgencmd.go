package system

import (
	"context"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
)

// vcRunner runs a vcgencmd subcommand and returns its stdout. Injectable so
// the parsers and the collect glue can be tested without the binary.
type vcRunner func(ctx context.Context, args ...string) (string, error)

func runVcgencmd(ctx context.Context, args ...string) (string, error) {
	out, err := exec.CommandContext(ctx, "vcgencmd", args...).Output()
	return string(out), err
}

// throttledFlags maps a get_throttled bit to its value channel. The low bits
// are live state; the 0x1_0000-and-up bits are sticky "occurred since boot".
var throttledFlags = []struct {
	bit uint64
	key string
}{
	{0x1, "undervolt_now"},
	{0x2, "freq_capped_now"},
	{0x4, "throttled_now"},
	{0x8, "soft_temp_now"},
	{0x10000, "undervolt_since_boot"},
	{0x20000, "freq_capped_since_boot"},
	{0x40000, "throttled_since_boot"},
	{0x80000, "soft_temp_since_boot"},
}

// parseThrottled decodes `throttled=0x...` into per-flag 0/1 values plus the
// raw bitmask as throttled_bits.
func parseThrottled(s string) (map[string]float64, bool) {
	s = strings.TrimSpace(s)
	i := strings.IndexByte(s, '=')
	if i < 0 {
		return nil, false
	}
	v, err := strconv.ParseUint(strings.TrimSpace(s[i+1:]), 0, 64)
	if err != nil {
		return nil, false
	}
	out := make(map[string]float64, len(throttledFlags)+1)
	out["throttled_bits"] = float64(v)
	for _, f := range throttledFlags {
		if v&f.bit != 0 {
			out[f.key] = 1
		} else {
			out[f.key] = 0
		}
	}
	return out, true
}

// pmicLine matches e.g. "     EXT5V_V volt(24)=5.02768000V" or
// "  VDD_CORE_A current(7)=1.09839000A".
var pmicLine = regexp.MustCompile(`^\s*(\S+)\s+(?:volt|current)\(\d+\)=([0-9.]+)[VA]\s*$`)

// parsePMIC returns the named PMIC ADC rails (e.g. EXT5V_V, VDD_CORE_V,
// BATT_V) mapped to their numeric value.
func parsePMIC(s string) map[string]float64 {
	out := make(map[string]float64, 32)
	for _, line := range strings.Split(s, "\n") {
		m := pmicLine.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		if v, err := strconv.ParseFloat(m[2], 64); err == nil {
			out[m[1]] = v
		}
	}
	return out
}

// collectVcgencmd fills supply/throttle channels via vcgencmd. Each call is
// bounded by ctx (the caller passes a short timeout) so a wedged vcgencmd
// cannot stall the poll loop. Absent binary or PMIC just omits the fields;
// the rpi_volt hwmon still provides a live undervolt_now.
func collectVcgencmd(ctx context.Context, vals map[string]float64, vc vcRunner) {
	if vc == nil {
		return
	}
	if out, err := vc(ctx, "get_throttled"); err == nil {
		if flags, ok := parseThrottled(out); ok {
			for k, v := range flags {
				vals[k] = v
			}
		}
	}
	if out, err := vc(ctx, "pmic_read_adc"); err == nil {
		rails := parsePMIC(out)
		if v, ok := rails["EXT5V_V"]; ok {
			vals["supply_v"] = v // 5V input rail — the aircraft-supply sag signal
		}
		if v, ok := rails["VDD_CORE_V"]; ok {
			vals["core_v"] = v
		}
		if v, ok := rails["BATT_V"]; ok {
			vals["rtc_batt_v"] = v // RTC coin cell health
		}
	}
}
