package clock

import (
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"sync"
)

// RTCStatus is the RTC backup-battery view. BatteryVoltage alone cannot prove
// the cell is connected — the constant-voltage trickle charger drives the
// sysfs node to its setpoint with or without a cell — so HeldTimeAtBoot
// carries the decisive evidence: whether the RTC delivered plausible time at
// kernel boot.
type RTCStatus struct {
	BatteryVoltage  float64 // volts; 0 when unreadable
	ChargingVoltage float64 // charger setpoint, volts; 0 when unreadable
	HeldTimeAtBoot  string  // "yes" | "no" | "unknown"
}

const rtcSysfsDir = "/sys/class/rtc/rtc0"

// QueryRTC reads the live battery node and the cached boot verdict.
func QueryRTC() RTCStatus {
	return RTCStatus{
		BatteryVoltage:  readMicrovolts(rtcSysfsDir + "/battery_voltage"),
		ChargingVoltage: readMicrovolts(rtcSysfsDir + "/charging_voltage"),
		HeldTimeAtBoot:  rtcBootVerdict(),
	}
}

func readMicrovolts(path string) float64 {
	b, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	uv, err := strconv.ParseFloat(strings.TrimSpace(string(b)), 64)
	if err != nil {
		return 0
	}
	return uv / 1e6
}

// reRTCBoot matches the rpi-rtc hctosys line, e.g.
// "rpi-rtc soc@107c000000:rpi_rtc: setting system clock to 1970-01-01T00:00:15 UTC (15)".
// The kernel logs it before systemd's clock-epoch floor advances a dead RTC's
// 1970 to a plausible-looking recent date, so the year here is trustworthy.
var reRTCBoot = regexp.MustCompile(`rpi_rtc: setting system clock to (\d{4})-`)

// rtcBootVerdict caches the dmesg parse: the ring buffer can rotate the boot
// line away on long uptimes, so the first read (triggered from startup via
// RTCStartupMeta) is kept for the life of the process.
var rtcBootVerdict = sync.OnceValue(func() string {
	out, err := exec.Command("dmesg").Output()
	if err != nil {
		return "unknown"
	}
	return parseRTCBootVerdict(string(out))
})

// parseRTCBootVerdict classifies the kernel's RTC hctosys line: a pre-2020
// year means the RTC lost time across the last power-off (dead or unseated
// backup battery). Absent line (rotated ring buffer, non-Pi host) → unknown.
func parseRTCBootVerdict(dmesg string) string {
	m := reRTCBoot.FindStringSubmatch(dmesg)
	if m == nil {
		return "unknown"
	}
	year, err := strconv.Atoi(m[1])
	if err != nil || year < 2020 {
		return "no"
	}
	return "yes"
}

// RTCStartupMeta returns clock_startup_* metadata rows for the RTC battery
// probe, persisted alongside StartupMeta's chrony keys.
func RTCStartupMeta(rtc RTCStatus) map[string]string {
	m := map[string]string{
		"clock_startup_rtc_held_time": rtc.HeldTimeAtBoot,
	}
	if rtc.BatteryVoltage > 0 {
		m["clock_startup_rtc_battery_v"] = strconv.FormatFloat(rtc.BatteryVoltage, 'f', 4, 64)
	}
	return m
}
