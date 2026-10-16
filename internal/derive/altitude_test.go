package derive

import (
	"math"
	"testing"

	"github.com/westphae/goflying/sensors/bmp280"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
)

func TestValidPressurePaBounds(t *testing.T) {
	cases := []struct {
		v    float64
		want bool
	}{
		{101325, true},  // standard sea level
		{50000, true},   // ~18000 ft
		{1013, false},   // hPa mistaken for Pa
		{101.3, false},  // kPa mistaken for Pa
		{250000, false}, // glitch / double kPa->Pa conversion
		{120000, false}, // just over the ceiling
		{math.NaN(), false},
	}
	for _, c := range cases {
		if got := validPressurePa(c.v); got != c.want {
			t.Errorf("validPressurePa(%v)=%v want %v", c.v, got, c.want)
		}
	}
}

func TestPressureAltitudeAtSeaLevelIsZero(t *testing.T) {
	got := bmp280.CalcAltitude(1013.25)
	if math.Abs(got) > 1.0 {
		t.Errorf("CalcAltitude(1013.25)=%.3f ft, want ~0", got)
	}
}

func TestPressureAltitudeAt500hPaIsAround18000Ft(t *testing.T) {
	got := bmp280.CalcAltitude(500.0)
	if got < 17500 || got > 18500 {
		t.Errorf("CalcAltitude(500)=%.0f ft, want ~18000", got)
	}
}

func TestFindPressurePa_prefersPodOverCabin(t *testing.T) {
	snap := live.Snapshot{
		Devices: map[string]live.Sample{
			"bmp581": {
				Device: "bmp581",
				Values: map[string]float64{pod.ChStaticP: 101_325},
			},
			"bmp280": {
				Device: "bmp280",
				Values: map[string]float64{"pressure_pa": 100_000},
			},
		},
	}
	pa, src, ok := findPressurePa(snap)
	if !ok {
		t.Fatal("expected pressure")
	}
	if src != PressureSourcePod {
		t.Fatalf("source=%v want pod", src)
	}
	if math.Abs(pa-101_325) > 1 {
		t.Fatalf("Pa=%v want ~101325", pa)
	}
}

func TestFindPressurePa_fallsBackToCabin(t *testing.T) {
	snap := live.Snapshot{
		Devices: map[string]live.Sample{
			"bmp280": {
				Device: "bmp280",
				Values: map[string]float64{"pressure_pa": 101_325},
			},
		},
	}
	pa, src, ok := findPressurePa(snap)
	if !ok {
		t.Fatal("expected pressure")
	}
	if src != PressureSourceCabin {
		t.Fatalf("source=%v want cabin", src)
	}
	if math.Abs(pa-101_325) > 1 {
		t.Fatalf("Pa=%v want ~101325", pa)
	}
}

func TestDensityAltFt_standardDaySeaLevel(t *testing.T) {
	paFt := bmp280.CalcAltitude(1013.25)
	da := DensityAltFt(paFt, 15.0)
	if math.Abs(da) > 50 {
		t.Errorf("DensityAltFt at ISA 15°C: got %.0f ft want ~0", da)
	}
}

func TestDensityAltFt_hotDayRaisesDA(t *testing.T) {
	paFt := bmp280.CalcAltitude(1013.25)
	da := DensityAltFt(paFt, 30.0)
	if da < 1000 || da > 2000 {
		t.Errorf("DensityAltFt at 30°C: got %.0f ft want ~1200", da)
	}
}

func TestIndicatedAltFt_usesKollsmanSetting(t *testing.T) {
	const paFt = 5000.0
	if got := IndicatedAltFt(paFt, 29.92); math.Abs(got-paFt) > 0.05 {
		t.Fatalf("standard: got %.2f want %.2f", got, paFt)
	}
	high := IndicatedAltFt(paFt, 30.12)
	delta := high - paFt
	// Hypsometric correction near 5000 ft is ~180 ft per 0.20 inHg.
	// The old 1000 ft/inHg rule returned exactly 200 and does not match a
	// panel altimeter built on the same standard atmosphere as pressure altitude.
	if delta < 150 || delta > 220 {
		t.Fatalf("+0.20 inHg at 5000 ft moved IA by %.1f ft, want ~180", delta)
	}
	low := paFt - IndicatedAltFt(paFt, 29.72)
	if low < 150 || low > 220 {
		t.Fatalf("-0.20 inHg at 5000 ft moved IA by %.1f ft, want ~180", low)
	}
	lib := bmp280.CalcAltitude(900)
	if math.Abs(IndicatedAltFt(lib, 29.92)-lib) > 1 {
		t.Fatalf("29.92 inHg disagrees with bmp280.CalcAltitude: lib=%.2f ind=%.2f", lib, IndicatedAltFt(lib, 29.92))
	}
}

func TestDensityAltFtFromPT_seaLevelAndHot(t *testing.T) {
	da, ok := DensityAltFtFromPT(101325, 15)
	if !ok || math.Abs(da) > 5 {
		t.Fatalf("ISA sea level DA=%v ok=%v want ~0", da, ok)
	}
	hot, ok := DensityAltFtFromPT(101325, 30)
	if !ok {
		t.Fatal("expected hot-day density altitude")
	}
	// 120 ft/°C rule gives 1800 ft. Tropospheric density altitude is lower.
	if hot < 1600 || hot > 1850 {
		t.Fatalf("30°C sea-level DA=%.0f ft, want ~1720", hot)
	}
}

func TestFindOATC_pod(t *testing.T) {
	snap := live.Snapshot{
		Devices: map[string]live.Sample{
			"bmp581": {
				Values: map[string]float64{
					pod.ChStaticP:    101_325,
					pod.ChStaticTemp: 22.0,
				},
			},
		},
	}
	c, ok := findOATC(snap, PressureSourcePod)
	if !ok || math.Abs(c-22) > 0.01 {
		t.Fatalf("got %v ok=%v", c, ok)
	}
}

func TestFindOATC_pod_milliC(t *testing.T) {
	snap := live.Snapshot{
		Devices: map[string]live.Sample{
			"bmp581": {
				Values: map[string]float64{
					pod.ChStaticTemp: 22_000,
				},
			},
		},
	}
	c, ok := findOATC(snap, PressureSourcePod)
	if !ok || math.Abs(c-22) > 0.1 {
		t.Fatalf("got %v ok=%v", c, ok)
	}
}

func TestFindPressurePa_legacyKPa(t *testing.T) {
	snap := live.Snapshot{
		Devices: map[string]live.Sample{
			"bmp280": {
				Device: "bmp280",
				Values: map[string]float64{"pressure": 101.325},
			},
		},
	}
	pa, _, ok := findPressurePa(snap)
	if !ok {
		t.Fatal("expected pressure")
	}
	if math.Abs(pa-101_325) > 1 {
		t.Fatalf("Pa=%v want ~101325", pa)
	}
}
