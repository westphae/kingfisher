package derive

import (
	"math"
	"testing"

	"github.com/westphae/goflying/sensors/bmp280"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
)

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
			pod.DeviceName: {
				Device: pod.DeviceName,
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

func TestFindOATC_pod(t *testing.T) {
	snap := live.Snapshot{
		Devices: map[string]live.Sample{
			pod.DeviceName: {
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
			pod.DeviceName: {
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
