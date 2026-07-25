package clock

import "testing"

func TestParseRTCBootVerdict(t *testing.T) {
	cases := []struct {
		name, dmesg, want string
	}{
		{"dead RTC", "[    0.410442] rpi-rtc soc@107c000000:rpi_rtc: setting system clock to 1970-01-01T00:00:15 UTC (15)", "no"},
		{"held time", "[    0.409117] rpi-rtc soc@107c000000:rpi_rtc: setting system clock to 2026-07-16T23:29:28 UTC (1784244568)", "yes"},
		{"no line", "[    2.739107] vc4-drm axi:gpu: bound 107c410000.pixelvalve", "unknown"},
		{"empty", "", "unknown"},
	}
	for _, c := range cases {
		if got := parseRTCBootVerdict(c.dmesg); got != c.want {
			t.Errorf("%s: got %q want %q", c.name, got, c.want)
		}
	}
}

func TestRTCStartupMeta(t *testing.T) {
	m := RTCStartupMeta(RTCStatus{BatteryVoltage: 2.9863, HeldTimeAtBoot: "no"})
	if m["clock_startup_rtc_held_time"] != "no" {
		t.Fatalf("held_time = %q", m["clock_startup_rtc_held_time"])
	}
	if m["clock_startup_rtc_battery_v"] != "2.9863" {
		t.Fatalf("battery_v = %q", m["clock_startup_rtc_battery_v"])
	}
	if _, ok := RTCStartupMeta(RTCStatus{HeldTimeAtBoot: "unknown"})["clock_startup_rtc_battery_v"]; ok {
		t.Fatal("battery_v key present with unreadable voltage")
	}
}
