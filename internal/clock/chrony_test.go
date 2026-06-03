package clock

import (
	"testing"
	"time"
)

const sampleTrackingPPS = `Reference ID    : 50505300 (PPS)
Stratum         : 1
Ref time (UTC)  : Sat May 30 18:14:41 2026
System time     : 0.000000029 seconds fast of NTP time
Last offset     : +0.000000030 seconds
RMS offset      : 0.000000188 seconds
Frequency       : 7.720 ppm fast
Residual freq   : +0.000 ppm
Skew            : 0.011 ppm
Root delay      : 0.000000001 seconds
Root dispersion : 0.000011265 seconds
Update interval : 16.0 seconds
Leap status     : Normal
`

const sampleSourcesPPS = `
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#- GPS                           0   4   377    11  -4571us[-4571us] +/-  150ms
#* PPS                           0   4   377    10    +12ns[  +33ns] +/-  101ns
`

const sampleTrackingUnsynced = `Reference ID    : 00000000 ()
Stratum         : 0
Last offset     : +0.000000000 seconds
RMS offset      : 0.000000000 seconds
`

const sampleSourcesUnsynced = `
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#x GPS                           0   4   377    22   -572ms[ -572ms] +/-  150ms
#x PPS                           0   4   377    23    -29us[  -29us] +/-  506ns
`

const sampleSourceStats = `Name/IP Address            NP  NR  Span  Frequency  Freq Skew  Offset  Std Dev
==============================================================================
GPS                        18  11   274    -11.533     16.341   -573ms  1432us
PPS                         8   5   110     -0.087      0.049    -31us   998ns
`

func TestParseTrackingPPS(t *testing.T) {
	var st DisciplineStatus
	parseTracking(sampleTrackingPPS, &st)
	if st.Source != SourcePPS || st.SourceLabel != "PPS" {
		t.Fatalf("source=%q label=%q", st.Source, st.SourceLabel)
	}
	if st.Stratum != 1 {
		t.Fatalf("stratum=%d", st.Stratum)
	}
	if st.LastOffset != 29 && st.LastOffset != 30 {
		t.Fatalf("last offset=%v want ~30ns", st.LastOffset)
	}
	if st.RMSOffset != 188 {
		t.Fatalf("rms offset=%v want 188ns", st.RMSOffset)
	}
	if !st.Synced {
		t.Fatal("expected synced")
	}
}

func TestParseActiveSourcePPS(t *testing.T) {
	var st DisciplineStatus
	parseActiveSource(sampleSourcesPPS, &st)
	if !st.PPSSteering {
		t.Fatal("expected PPS steering")
	}
	if st.Source != SourcePPS {
		t.Fatalf("source=%q", st.Source)
	}
}

func TestParseTrackingUnsynced(t *testing.T) {
	var st DisciplineStatus
	parseTracking(sampleTrackingUnsynced, &st)
	if st.Synced {
		t.Fatal("expected unsynced")
	}
	if st.Source != SourceLocal {
		t.Fatalf("source=%q want local", st.Source)
	}
}

func TestParseActiveSourceGPS(t *testing.T) {
	var st DisciplineStatus
	parseActiveSource(sampleSourcesGPS, &st)
	if st.PPSSteering {
		t.Fatal("expected no PPS steering")
	}
	if st.Source != SourceGPS {
		t.Fatalf("source=%q", st.Source)
	}
}

func TestParseSourceLinesUnsynced(t *testing.T) {
	var st DisciplineStatus
	parseSourceLines(sampleSourcesUnsynced, &st)
	if st.GPSState != SourceStateError {
		t.Fatalf("gps state=%q", st.GPSState)
	}
	if st.PPSState != SourceStateError {
		t.Fatalf("pps state=%q", st.PPSState)
	}
}

func TestParseSourceStats(t *testing.T) {
	var st DisciplineStatus
	parseSourceStats(sampleSourceStats, &st)
	if st.GPSOffsetMs > -572 || st.GPSOffsetMs < -574 {
		t.Fatalf("gps offset ms=%v want ~-573", st.GPSOffsetMs)
	}
	if st.PPSOffsetMs > -0.02 || st.PPSOffsetMs < -0.04 {
		t.Fatalf("pps offset ms=%v want ~-0.031", st.PPSOffsetMs)
	}
}

func TestParseChronyDuration(t *testing.T) {
	cases := map[string]time.Duration{
		"-573ms": -573 * time.Millisecond,
		"+620ms": 620 * time.Millisecond,
		"-31us":  -31 * time.Microsecond,
		"+12ns":  12 * time.Nanosecond,
	}
	for s, want := range cases {
		if got := parseChronyDuration(s); got != want {
			t.Fatalf("%q: got %v want %v", s, got, want)
		}
	}
}

const sampleSourcesGPS = `
#* GPS                           0   4   377    11  -4571us[-4571us] +/-  150ms
`

func TestStartupMetaPPS(t *testing.T) {
	disc := DisciplineStatus{
		Available:   true,
		Synced:      true,
		Source:      SourcePPS,
		SourceLabel: "PPS",
		Stratum:     1,
		PPSPresent:  true,
		PPSSteering: true,
	}
	m := StartupMeta(disc)
	if m["clock_startup_pps_present"] != "true" || m["clock_startup_pps_steering"] != "true" {
		t.Fatalf("pps meta: %+v", m)
	}
	if m["clock_startup_chrony_source"] != SourcePPS || m["clock_startup_chrony_stratum"] != "1" {
		t.Fatalf("chrony meta: %+v", m)
	}
}

func TestStartupMetaChronyUnavailable(t *testing.T) {
	m := StartupMeta(DisciplineStatus{PPSPresent: true})
	if m["clock_startup_pps_present"] != "true" {
		t.Fatalf("pps_present: %+v", m)
	}
	if m["clock_startup_chrony_available"] != "false" {
		t.Fatalf("expected chrony unavailable: %+v", m)
	}
	if _, ok := m["clock_startup_chrony_synced"]; ok {
		t.Fatalf("unexpected synced key: %+v", m)
	}
}

func TestClassifySourceLabel(t *testing.T) {
	cases := map[string]string{
		"PPS":            SourcePPS,
		"GPS":            SourceGPS,
		"pool.ntp.org":   SourceNTP,
		"":               SourceLocal,
	}
	for label, want := range cases {
		if got := classifySourceLabel(label); got != want {
			t.Fatalf("label %q: got %q want %q", label, got, want)
		}
	}
}
