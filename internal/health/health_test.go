package health

import (
	"strings"
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/clock"
	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
	"github.com/westphae/kingfisher/internal/store"
	"github.com/westphae/kingfisher/internal/ups"
)

func check(r Report, id string) Check {
	for _, c := range r.Checks {
		if c.ID == id {
			return c
		}
	}
	return Check{}
}

func TestPPS_steeringOK(t *testing.T) {
	r := Evaluate(GatherIn{
		Clock: clock.DisciplineStatus{
			Available: true, Synced: true, Source: clock.SourcePPS, PPSPresent: true, PPSSteering: true,
		},
		GPSFix:   gps.Fix{HasFix: true, Mode: 3},
		GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "pps"); c.Level != LevelOK {
		t.Fatalf("pps=%+v", c)
	}
}

func TestPPS_gpsOnlyWarn(t *testing.T) {
	r := Evaluate(GatherIn{
		Clock: clock.DisciplineStatus{
			Available: true, Synced: true, Source: clock.SourceGPS, PPSPresent: true, PPSSteering: false,
		},
		GPSFix:   gps.Fix{HasFix: true, Mode: 3},
		GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "pps"); c.Level != LevelWarn || c.Detail != "GPS only" {
		t.Fatalf("pps=%+v", c)
	}
}

func TestPPS_unsyncedFail(t *testing.T) {
	r := Evaluate(GatherIn{
		Clock: clock.DisciplineStatus{
			Available: true, Synced: false, PPSPresent: true, PPSState: clock.SourceStateError,
		},
	})
	if c := check(r, "pps"); c.Level != LevelFail {
		t.Fatalf("pps=%+v", c)
	}
}

func TestGPS_noFixFail_2DWarn(t *testing.T) {
	r := Evaluate(GatherIn{})
	if c := check(r, "gps"); c.Level != LevelFail {
		t.Fatalf("no fix: %+v", c)
	}
	r = Evaluate(GatherIn{
		GPSFix:   gps.Fix{HasFix: true, Mode: 2},
		GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
		Clock:    clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
	})
	if c := check(r, "gps"); c.Level != LevelWarn || c.Detail != "2D" {
		t.Fatalf("2D: %+v", c)
	}
}

func TestPOD_burstQuietOK_lostFail(t *testing.T) {
	r := Evaluate(GatherIn{
		Pod:    pod.LinkStats{Enabled: true, PowerMode: "burst", BurstQuiet: true},
		Clock:  clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix: gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "pod"); c.Level != LevelOK {
		t.Fatalf("burst quiet: %+v", c)
	}
	r = Evaluate(GatherIn{
		Pod:    pod.LinkStats{Enabled: true, BurstLost: true, PowerMode: "burst"},
		Clock:  clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix: gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "pod"); c.Level != LevelFail || c.Detail != "lost" {
		t.Fatalf("burst lost: %+v", c)
	}
}

func TestSYS_undervoltNowFail_stickyWarn(t *testing.T) {
	now := time.Now()
	r := Evaluate(GatherIn{
		Now: now,
		Hub: live.Snapshot{ServerTsNs: now.UnixNano(), Devices: map[string]live.Sample{
			"system": {Device: "system", TsNs: now.UnixNano(), Values: map[string]float64{"undervolt_now": 1}},
		}},
		Clock:  clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix: gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "sys"); c.Level != LevelFail {
		t.Fatalf("undervolt now: %+v", c)
	}
	r = Evaluate(GatherIn{
		Now: now,
		Hub: live.Snapshot{ServerTsNs: now.UnixNano(), Devices: map[string]live.Sample{
			"system": {Device: "system", TsNs: now.UnixNano(), Values: map[string]float64{"undervolt_since_boot": 1}},
		}},
		Clock:  clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix: gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "sys"); c.Level != LevelWarn {
		t.Fatalf("sticky: %+v", c)
	}
}

func TestUPS_acOK_lowFail(t *testing.T) {
	r := Evaluate(GatherIn{
		UPS:      ups.Snapshot{Enabled: true, Present: true, PLDOk: true, ACOk: true},
		UPSWarnS: 1800,
		Clock:    clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix:   gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "ups"); c.Level != LevelOK {
		t.Fatalf("AC: %+v", c)
	}
	r = Evaluate(GatherIn{
		UPS:      ups.Snapshot{Enabled: true, Present: true, PLDOk: true, ACOk: false, TimeRemainingS: 600},
		UPSWarnS: 1800,
		Clock:    clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix:   gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "ups"); c.Level != LevelFail {
		t.Fatalf("low: %+v", c)
	}
	if r.Energy.UPS != "10m" {
		t.Fatalf("energy UPS=%q", r.Energy.UPS)
	}
}

func TestREC_pausedWarn_diskFail(t *testing.T) {
	r := Evaluate(GatherIn{
		Recording: store.RecordingState{Paused: true},
		Clock:     clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix:    gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "rec"); c.Level != LevelWarn {
		t.Fatalf("paused: %+v", c)
	}
	low := int64(100)
	r = Evaluate(GatherIn{
		DiskFree: &low,
		Clock:    clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix:   gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
	})
	if c := check(r, "rec"); c.Level != LevelFail {
		t.Fatalf("disk: %+v", c)
	}
}

func TestMissing_enabledIIO_andDerivedOnlyIfInputsFresh(t *testing.T) {
	now := time.Now()
	cfg := config.Defaults()
	cfg.AHRS.Enabled = true
	cfg.Compass.Enabled = true
	cfg.Devices = map[string]config.Device{
		"icm45686-accel": {Enabled: true, SampleHz: 100},
	}
	r := Evaluate(GatherIn{
		Now: now, Cfg: cfg, IIONames: []string{"icm45686-accel"}, ExpectGPS: true,
		Clock:  clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix: gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
		Hub: live.Snapshot{ServerTsNs: now.UnixNano(), Devices: map[string]live.Sample{
			"gps":    {Device: "gps", TsNs: now.UnixNano(), Values: map[string]float64{"fix": 1}},
			"system": {Device: "system", TsNs: now.UnixNano(), Values: map[string]float64{"supply_v": 5}},
		}},
	})
	joined := strings.Join(r.Missing, ",")
	if !strings.Contains(joined, "icm45686-accel") {
		t.Fatalf("want missing accel, got %v", r.Missing)
	}
	if strings.Contains(joined, "ahrs") {
		t.Fatalf("ahrs should not be required without IMU: %v", r.Missing)
	}
	if strings.Contains(joined, "airspeed") {
		t.Fatalf("airspeed should not be required without ms4525: %v", r.Missing)
	}

	r = Evaluate(GatherIn{
		Now: now, Cfg: cfg, IIONames: []string{"icm45686-accel"},
		Clock:  clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix: gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
		Hub: live.Snapshot{ServerTsNs: now.UnixNano(), Devices: map[string]live.Sample{
			"icm45686-accel": {Device: "icm45686-accel", TsNs: now.UnixNano(), Values: map[string]float64{"accel_x": 0}},
			"system":         {Device: "system", TsNs: now.UnixNano(), Values: map[string]float64{"supply_v": 5}},
		}},
	})
	joined = strings.Join(r.Missing, ",")
	if !strings.Contains(joined, "ahrs") {
		t.Fatalf("ahrs required when IMU fresh: %v", r.Missing)
	}
}

func TestAlertLine_allOK_andMiss(t *testing.T) {
	now := time.Now()
	r := Evaluate(GatherIn{
		Now:    now,
		Clock:  clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix: gps.Fix{HasFix: true, Mode: 3}, GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
		Hub: live.Snapshot{ServerTsNs: now.UnixNano(), Devices: map[string]live.Sample{
			"system": {Device: "system", TsNs: now.UnixNano(), Values: map[string]float64{"supply_v": 5}},
		}},
	})
	text, fail := AlertLine(r, 0)
	if fail || text != "ALL OK" {
		t.Fatalf("got %q fail=%v worst=%+v", text, fail, r.Worst)
	}
}

func TestFormatHours(t *testing.T) {
	if got := formatHours(4.1 * 3600); got != "4.1h" {
		t.Fatalf("got %q", got)
	}
	if got := formatHours(12 * 60); got != "12m" {
		t.Fatalf("got %q", got)
	}
}
