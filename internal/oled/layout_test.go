package oled

import (
	"strings"
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/clock"
	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/health"
	"github.com/westphae/kingfisher/internal/live"
)

func healthyReport() health.Report {
	now := time.Now()
	ns := now.UnixNano()
	return health.Evaluate(health.GatherIn{
		Now:      now,
		Clock:    clock.DisciplineStatus{Available: true, Synced: true, PPSSteering: true},
		GPSFix:   gps.Fix{HasFix: true, Mode: 3},
		GPSClock: gps.ClockStatus{HasFix: true, Fresh: true},
		Hub: live.Snapshot{ServerTsNs: ns, Devices: map[string]live.Sample{
			"system": {Device: "system", TsNs: ns, Values: map[string]float64{"supply_v": 5}},
		}},
	})
}

func TestRender_allOKHasAlertAndGlyphs(t *testing.T) {
	f := Render(View{
		Health:    healthyReport(),
		ClockHHMM: "14:32",
		Tail:      "N123KF",
	})
	dump := f.Dump()
	if !strings.Contains(dump, "#") {
		t.Fatal("empty frame")
	}
	if !f.RowHasPixels(0, 7) {
		t.Fatal("glyph row empty")
	}
	if !f.RowHasPixels(24, 39) {
		t.Fatal("alert row empty")
	}
	if !f.RowHasPixels(56, 63) {
		t.Fatal("footer empty")
	}
}

func TestRender_failInvertsAlertBand(t *testing.T) {
	rep := health.Evaluate(health.GatherIn{}) // no GPS → fail
	ok := Render(View{Health: healthyReport()})
	bad := Render(View{Health: rep})
	if !bad.RowHasPixels(22, 39) {
		t.Fatal("fail alert empty")
	}
	// Fail band is filled (many more pixels than the OK outline text).
	var nOK, nBad int
	for y := 22; y <= 39; y++ {
		for x := 0; x < width; x++ {
			p, bit := y/8, byte(1)<<uint(y%8)
			if ok.Pages[p][x]&bit != 0 {
				nOK++
			}
			if bad.Pages[p][x]&bit != 0 {
				nBad++
			}
		}
	}
	if nBad <= nOK {
		t.Fatalf("expected filled fail band (bad=%d ok=%d)", nBad, nOK)
	}
}

func TestFormatCycle(t *testing.T) {
	hub := live.Snapshot{Devices: map[string]live.Sample{
		"airspeed": {Values: map[string]float64{"ias_kt": 142.2}},
	}}
	got := FormatCycle(hub, config.OLEDCycleItem{Device: "airspeed", Channel: "ias_kt"})
	if !strings.Contains(got, "142") || !strings.Contains(got, "KT") {
		t.Fatalf("got %q", got)
	}
}

func TestText6x8_knownGlyph(t *testing.T) {
	var f Frame
	f.Text6x8(0, 0, "A")
	if !f.RowHasPixels(0, 6) {
		t.Fatal("A produced no pixels")
	}
}

func TestDirtyPagesEqual(t *testing.T) {
	a := Render(View{Health: healthyReport(), ClockHHMM: "00:00"})
	b := Render(View{Health: healthyReport(), ClockHHMM: "00:00"})
	if a.Pages != b.Pages {
		t.Fatal("deterministic render")
	}
}
