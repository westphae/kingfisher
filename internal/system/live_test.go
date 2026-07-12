package system

import (
	"context"
	"os"
	"sort"
	"testing"
)

// TestCollectLive exercises the real collectors against this host's /proc,
// /sys, and vcgencmd. It is a bring-up/diagnostic test (the Pi is the
// deployment target), skipped where /proc is absent.
func TestCollectLive(t *testing.T) {
	if _, err := os.Stat("/proc/loadavg"); err != nil {
		t.Skip("no /proc; not a Linux host")
	}
	m := &Monitor{last: make(map[string]float64)}
	m.hwmon = discoverHwmon()

	vals := make(map[string]float64, 40)
	idle, total, ok := collectProc(vals, 0, 0, false)
	collectHwmon(vals, m.hwmon)
	m.prevIdle, m.prevTotal, m.havePrev = idle, total, ok
	collectVcgencmd(context.Background(), vals, runVcgencmd)
	m.collectSelf(vals)

	keys := make([]string, 0, len(vals))
	for k := range vals {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		t.Logf("%-24s %v", k, vals[k])
	}

	for _, k := range []string{"cpu_temp_c", "load_1m", "uptime_s", "goroutines"} {
		if _, ok := vals[k]; !ok {
			t.Errorf("missing expected channel %q", k)
		}
	}
	if v := vals["cpu_temp_c"]; v < 10 || v > 120 {
		t.Errorf("implausible cpu_temp_c=%v", v)
	}
}
