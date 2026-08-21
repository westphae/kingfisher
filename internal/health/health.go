// Package health grades kingfisher hardware for the OLED glance panel and
// /api/status. Rules match the cockpit chips (clock/PPS, pod, UPS, system
// undervolt, recording) so the two views cannot disagree.
package health

import (
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/westphae/kingfisher/internal/clock"
	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/store"
	"github.com/westphae/kingfisher/internal/ups"
)

const (
	// StaleAfter is how old a hub sample may be before the device is missing.
	// Matches the cockpit UI STALENESS_MS window.
	StaleAfter = 4 * time.Second

	// DefaultUPSWarnS is the on-battery remaining-time floor (30 min).
	DefaultUPSWarnS = 1800.0

	// DiskCriticalBytes is REC-fail free space (512 MiB).
	DiskCriticalBytes int64 = 512 << 20

	weakRSSI = -78
)

// Level is OK / warn / fail for a single glyph.
type Level string

const (
	LevelOK   Level = "ok"
	LevelWarn Level = "warn"
	LevelFail Level = "fail"
)

// Check is one always-on health glyph.
type Check struct {
	ID     string `json:"id"`
	Level  Level  `json:"level"`
	Label  string `json:"label"`
	Detail string `json:"detail"`
}

// Energy is the hours-remaining line on the OLED.
type Energy struct {
	UPS string `json:"ups"`
	POD string `json:"pod"`
}

// Report is the evaluated health snapshot.
type Report struct {
	Checks  []Check  `json:"checks"`
	Missing []string `json:"missing,omitempty"`
	Worst   *Check   `json:"worst,omitempty"`
	Energy  Energy   `json:"energy"`
}

// GatherIn is everything Evaluate needs, collected by the OLED loop and /api/status.
type GatherIn struct {
	Now        time.Time
	StaleAfter time.Duration
	UPSWarnS   float64

	Hub        live.Snapshot
	Recording  store.RecordingState
	DiskFree   *int64
	Clock      clock.DisciplineStatus
	GPSFix     gps.Fix
	GPSClock   gps.ClockStatus
	ExpectGPS  bool
	Pod        pod.LinkStats
	PodDevices []string
	UPS        ups.Snapshot
	IIONames   []string
	Cfg        *config.Config
}

// Evaluate grades REC/PPS/GPS/POD/UPS/SYS plus missing sensors.
func Evaluate(in GatherIn) Report {
	if in.Now.IsZero() {
		in.Now = time.Now()
	}
	if in.StaleAfter <= 0 {
		in.StaleAfter = StaleAfter
	}
	if in.UPSWarnS <= 0 {
		in.UPSWarnS = DefaultUPSWarnS
	}
	cfg := in.Cfg
	if cfg == nil {
		cfg = config.Defaults()
	}

	sysVals := map[string]float64{}
	if sm, ok := in.Hub.Devices["system"]; ok {
		sysVals = sm.Values
	}

	checks := []Check{
		evalREC(in.Recording, in.DiskFree),
		evalPPS(in.Clock),
		evalGPS(in.GPSFix, in.GPSClock),
		evalPOD(in.Pod),
		evalUPS(in.UPS, in.UPSWarnS),
		evalSYS(sysVals),
	}
	missing := missingDevices(in, cfg)
	rep := Report{
		Checks:  checks,
		Missing: missing,
		Energy:  energy(in.UPS, in.Pod),
	}
	rep.Worst = worst(checks, missing)
	return rep
}

func evalREC(rec store.RecordingState, diskFree *int64) Check {
	c := Check{ID: "rec", Label: "REC", Level: LevelOK, Detail: "recording"}
	if diskFree != nil && *diskFree < DiskCriticalBytes {
		c.Level = LevelFail
		c.Detail = "disk low"
		return c
	}
	if rec.Degraded {
		c.Level = LevelFail
		c.Detail = "store wedged"
		if rec.LastError != "" {
			c.Detail = rec.LastError
		}
		return c
	}
	if rec.Paused {
		c.Level = LevelWarn
		c.Detail = "paused"
		return c
	}
	return c
}

func evalPPS(d clock.DisciplineStatus) Check {
	c := Check{ID: "pps", Label: "PPS", Level: LevelOK, Detail: "steering"}
	if !d.Available {
		c.Level = LevelFail
		c.Detail = "no chrony"
		return c
	}
	if !d.Synced {
		c.Level = LevelFail
		c.Detail = "unsynced"
		if d.PPSPresent && d.PPSState == clock.SourceStateError {
			c.Detail = "PPS error"
		}
		return c
	}
	if d.PPSPresent && d.PPSState == clock.SourceStateError {
		c.Level = LevelFail
		c.Detail = "PPS error"
		return c
	}
	if d.PPSSteering {
		return c
	}
	c.Level = LevelWarn
	switch d.Source {
	case clock.SourceGPS:
		c.Detail = "GPS only"
	case clock.SourceNTP:
		c.Detail = "NTP"
	default:
		c.Detail = "not steering"
	}
	return c
}

func evalGPS(fix gps.Fix, st gps.ClockStatus) Check {
	c := Check{ID: "gps", Label: "GPS", Level: LevelOK, Detail: "3D"}
	has := fix.HasFix || st.HasFix
	if !has {
		c.Level = LevelFail
		c.Detail = "no fix"
		return c
	}
	fresh := st.Fresh
	if st.State == gps.ClockStateStale {
		fresh = false
	}
	mode := fix.Mode
	if mode >= 3 && fresh {
		c.Detail = "3D"
		return c
	}
	c.Level = LevelWarn
	switch {
	case !fresh:
		c.Detail = "stale"
	case mode == 2:
		c.Detail = "2D"
	default:
		c.Detail = "aging"
	}
	return c
}

func evalPOD(p pod.LinkStats) Check {
	c := Check{ID: "pod", Label: "POD", Level: LevelOK, Detail: "up"}
	if !p.Enabled {
		c.Detail = "off"
		return c
	}
	if p.ProtectSleep {
		c.Level = LevelFail
		c.Detail = "protect"
		return c
	}
	if p.BurstLost {
		c.Level = LevelFail
		c.Detail = "lost"
		return c
	}
	if !p.Connected && p.PowerMode == "burst" {
		if p.BurstQuiet {
			c.Detail = "burst"
			return c
		}
		c.Level = LevelWarn
		c.Detail = "burst overdue"
		return c
	}
	if !p.Connected {
		c.Level = LevelFail
		c.Detail = "silent"
		return c
	}
	if p.HasRssi && int(p.RssiDBm) < weakRSSI {
		c.Level = LevelWarn
		c.Detail = "weak RSSI"
		return c
	}
	if p.RecentDrops {
		c.Level = LevelWarn
		c.Detail = "drops"
		return c
	}
	if p.HasRssi {
		c.Detail = "up"
	}
	return c
}

func evalUPS(u ups.Snapshot, warnS float64) Check {
	c := Check{ID: "ups", Label: "UPS", Level: LevelOK, Detail: "AC"}
	if !u.Enabled {
		c.Detail = "off"
		return c
	}
	if !u.Present {
		c.Level = LevelFail
		c.Detail = "no gauge"
		return c
	}
	onBatt := u.PLDOk && !u.ACOk
	if !onBatt {
		if u.PLDOk {
			c.Detail = "AC"
		} else {
			c.Level = LevelWarn
			c.Detail = "AC ?"
		}
		return c
	}
	if u.TimeRemainingS >= 0 && u.TimeRemainingS < warnS {
		c.Level = LevelFail
		c.Detail = "low"
		return c
	}
	c.Level = LevelWarn
	c.Detail = "battery"
	return c
}

func evalSYS(v map[string]float64) Check {
	c := Check{ID: "sys", Label: "SYS", Level: LevelOK, Detail: "ok"}
	if len(v) == 0 {
		c.Level = LevelWarn
		c.Detail = "no sys"
		return c
	}
	if flagOn(v, "undervolt_now") {
		c.Level = LevelFail
		c.Detail = "undervolt"
		return c
	}
	if flagOn(v, "throttled_now") {
		c.Level = LevelFail
		c.Detail = "throttled"
		return c
	}
	if flagOn(v, "soft_temp_now") {
		c.Level = LevelFail
		c.Detail = "overtemp"
		return c
	}
	if flagOn(v, "undervolt_since_boot") {
		c.Level = LevelWarn
		c.Detail = "UV since boot"
		return c
	}
	if flagOn(v, "throttled_since_boot") {
		c.Level = LevelWarn
		c.Detail = "throttle since boot"
		return c
	}
	if flagOn(v, "soft_temp_since_boot") {
		c.Level = LevelWarn
		c.Detail = "overtemp since boot"
		return c
	}
	return c
}

func flagOn(v map[string]float64, k string) bool {
	x, ok := v[k]
	return ok && x >= 1
}

func missingDevices(in GatherIn, cfg *config.Config) []string {
	staleAfter := in.StaleAfter
	nowNs := in.Now.UnixNano()
	if in.Hub.ServerTsNs > 0 {
		nowNs = in.Hub.ServerTsNs
	}
	fresh := func(name string) bool {
		sm, ok := in.Hub.Devices[name]
		if !ok || sm.TsNs == 0 {
			return false
		}
		return nowNs-sm.TsNs <= int64(staleAfter)
	}

	disabled := map[string]bool{}
	expect := map[string]bool{}
	if cfg.Devices != nil {
		for name, d := range cfg.Devices {
			if !d.Enabled {
				disabled[name] = true
				continue
			}
			expect[name] = true
		}
	}
	for _, name := range in.IIONames {
		if disabled[name] || name == "" || name == pod.DeviceName {
			continue
		}
		if cfg.DeviceOrDefault(name, 10).Enabled {
			expect[name] = true
		}
	}
	delete(expect, pod.DeviceName)

	if in.ExpectGPS {
		expect["gps"] = true
	}
	if cfg.System.EnabledEffective() {
		expect["system"] = true
	}
	if cfg.UPS.Enabled {
		expect["ups"] = true
	}

	podLinkOK := in.Pod.Enabled && (in.Pod.Connected || in.Pod.BurstQuiet) && !in.Pod.ProtectSleep && !in.Pod.BurstLost
	if podLinkOK {
		for _, name := range in.PodDevices {
			if name != "" {
				expect[name] = true
			}
		}
	}

	imuOK := false
	for name, sm := range in.Hub.Devices {
		if looksLikeIMU(sm) && fresh(name) {
			imuOK = true
			break
		}
	}
	if cfg.AHRS.Enabled && imuOK {
		expect["ahrs"] = true
	}

	mag := strings.TrimSpace(cfg.Compass.Magnetometer)
	if mag == "" {
		mag = pod.DefaultDeviceName(wire.SensorMag)
	}
	if cfg.Compass.Enabled && fresh(mag) {
		expect["compass"] = true
	}
	if fresh("ms4525") {
		expect["airspeed"] = true
	}

	var missing []string
	for name := range expect {
		if !fresh(name) {
			missing = append(missing, name)
		}
	}
	slices.Sort(missing)
	return missing
}

func looksLikeIMU(sm live.Sample) bool {
	for k := range sm.Values {
		if strings.HasPrefix(k, "accel_") || strings.HasPrefix(k, "anglvel_") {
			return true
		}
	}
	return false
}

func worst(checks []Check, missing []string) *Check {
	for i := range checks {
		if checks[i].Level == LevelFail {
			c := checks[i]
			return &c
		}
	}
	if len(missing) > 0 {
		c := Check{ID: "miss", Label: "MISS", Level: LevelFail, Detail: missing[0]}
		return &c
	}
	for i := range checks {
		if checks[i].Level == LevelWarn {
			c := checks[i]
			return &c
		}
	}
	return nil
}

func energy(u ups.Snapshot, p pod.LinkStats) Energy {
	e := Energy{UPS: "-", POD: "-"}
	if u.Enabled && u.Present {
		onBatt := u.PLDOk && !u.ACOk
		if !onBatt && u.PLDOk {
			e.UPS = "AC"
		} else if onBatt && u.TimeRemainingS >= 0 {
			e.UPS = formatHours(u.TimeRemainingS)
		} else if u.SocPct > 0 {
			e.UPS = formatPct(u.SocPct)
		}
	} else if u.Enabled {
		e.UPS = "?"
	}
	if p.HasBatteryTelemetry && p.BatteryGaugeLearned && p.BatteryTimeRemainS > 0 {
		e.POD = formatHours(float64(p.BatteryTimeRemainS))
	} else if p.HasBatteryTelemetry && p.BatteryGaugeLearned && p.BatterySocPct > 0 {
		e.POD = formatPct(float64(p.BatterySocPct))
	} else if p.HasBattery && p.BatteryV > 0 {
		e.POD = formatVolts(float64(p.BatteryV))
	} else if !p.Enabled {
		e.POD = "off"
	}
	return e
}

func formatHours(sec float64) string {
	if sec < 0 {
		return "-"
	}
	if sec >= 3600 {
		return trim1(sec/3600) + "h"
	}
	m := int(sec/60 + 0.5)
	if m < 1 {
		m = 1
	}
	return strconv.Itoa(m) + "m"
}

func formatPct(p float64) string {
	return strconv.Itoa(int(p+0.5)) + "%"
}

func formatVolts(v float64) string {
	tenth := int(v*10 + 0.5)
	return strconv.Itoa(tenth/10) + "." + strconv.Itoa(tenth%10) + "v"
}

func trim1(h float64) string {
	tenth := int(h*10 + 0.5)
	if tenth%10 == 0 {
		return strconv.Itoa(tenth / 10)
	}
	return strconv.Itoa(tenth/10) + "." + strconv.Itoa(tenth%10)
}

// AlertLine is the 12×16 OLED message: ALL OK, a fail/warn detail, or MISS name.
func AlertLine(r Report, alertIdx int) (text string, fail bool) {
	fails := make([]Check, 0, len(r.Checks)+1)
	warns := make([]Check, 0, len(r.Checks))
	for _, c := range r.Checks {
		switch c.Level {
		case LevelFail:
			fails = append(fails, c)
		case LevelWarn:
			warns = append(warns, c)
		}
	}
	if len(r.Missing) > 0 {
		fails = append(fails, Check{ID: "miss", Label: "MISS", Detail: r.Missing[0]})
	}
	if len(fails) == 0 && len(warns) == 0 {
		return "ALL OK", false
	}
	list := fails
	if len(list) == 0 {
		list = warns
	}
	if alertIdx < 0 {
		alertIdx = 0
	}
	c := list[alertIdx%len(list)]
	text = strings.ToUpper(c.Label + " " + c.Detail)
	if c.ID == "miss" {
		text = "MISS " + strings.ToUpper(c.Detail)
	}
	if len(text) > 10 {
		text = text[:10]
	}
	return text, c.Level == LevelFail || c.ID == "miss"
}

// ExtraMissing is how many names beyond the first to show as "+N".
func ExtraMissing(r Report) int {
	if len(r.Missing) <= 1 {
		return 0
	}
	return len(r.Missing) - 1
}
