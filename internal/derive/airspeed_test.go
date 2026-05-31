package derive

import (
	"context"
	"math"
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
)

func TestCalcIASKt_zeroDp(t *testing.T) {
	if got := CalcIASKt(0); got != 0 {
		t.Fatalf("CalcIASKt(0)=%v want 0", got)
	}
	if got := CalcIASKt(-5); got != 0 {
		t.Fatalf("CalcIASKt(-5)=%v want 0", got)
	}
}

func TestCalcIASKt_seaLevel100Kt(t *testing.T) {
	vMps := 100.0 / mpsPerKt
	dp := 0.5 * rho0KgM3 * vMps * vMps
	got := CalcIASKt(dp)
	if math.Abs(got-100) > 0.5 {
		t.Fatalf("CalcIASKt(%.1f Pa)=%.2f kt want ~100", dp, got)
	}
}

func TestCorrectedDpPa_zeroOffset(t *testing.T) {
	if got := CorrectedDpPa(14, 14); got != 0 {
		t.Fatalf("got %v want 0", got)
	}
	if got := CorrectedDpPa(20, 14); math.Abs(got-6) > 1e-9 {
		t.Fatalf("got %v want 6", got)
	}
}

func TestApplyLowSpeedFloor(t *testing.T) {
	if got := ApplyLowSpeedFloor(4.9, 5); got != 0 {
		t.Fatalf("got %v want 0", got)
	}
	if got := ApplyLowSpeedFloor(5.1, 5); math.Abs(got-5.1) > 1e-9 {
		t.Fatalf("got %v want 5.1", got)
	}
}

func TestAirspeedProcessor_zeroAndFloor(t *testing.T) {
	var p airspeedProcessor
	emaOff := false
	s := airspeedSettingsFrom(config.Airspeed{
		DpZeroPa:        14,
		LowSpeedFloorKt: 5,
		EmaEnabled:      &emaOff,
	})
	dpCal, ias := p.process(14, s)
	if dpCal != 0 || ias != 0 {
		t.Fatalf("zeroed rest: dpCal=%v ias=%v", dpCal, ias)
	}
	_, ias = p.process(1621, s)
	if ias < 90 {
		t.Fatalf("expected high IAS after zero, got %v", ias)
	}
}

func TestEmaAlpha(t *testing.T) {
	a := emaAlpha(200*time.Millisecond, 500*time.Millisecond)
	if a <= 0 || a >= 1 {
		t.Fatalf("alpha=%v out of range", a)
	}
}

func TestCalcTASKt_hotDayRaisesTAS(t *testing.T) {
	ias := 100.0
	staticPa := 101_325.0
	coldTAS, ok := CalcTASKt(ias, staticPa, 15.0)
	if !ok {
		t.Fatal("expected TAS at 15°C")
	}
	hotTAS, ok := CalcTASKt(ias, staticPa, 30.0)
	if !ok {
		t.Fatal("expected TAS at 30°C")
	}
	if hotTAS <= coldTAS {
		t.Fatalf("hot TAS %.2f should exceed cold TAS %.2f", hotTAS, coldTAS)
	}
}

func TestCalcTASKt_missingInputs(t *testing.T) {
	if _, ok := CalcTASKt(100, 0, 15); ok {
		t.Fatal("expected false for invalid static pressure")
	}
	if _, ok := CalcTASKt(0, 101_325, 15); ok {
		t.Fatal("expected false for zero IAS")
	}
}

func TestFindPitot_present(t *testing.T) {
	snap := live.Snapshot{
		Devices: map[string]live.Sample{
			"ms4525": {
				Values: map[string]float64{
					pod.ChAirspeedDP:   500,
					pod.ChAirspeedTemp: 25,
				},
			},
		},
	}
	dp, temp, ok := findPitot(snap)
	if !ok {
		t.Fatal("expected pitot")
	}
	if math.Abs(dp-500) > 1e-9 || math.Abs(temp-25) > 1e-9 {
		t.Fatalf("dp=%v temp=%v", dp, temp)
	}
}
func TestSamplePitotDpAverage(t *testing.T) {
	hub := live.NewHub()
	stop := make(chan struct{})
	defer close(stop)
	go hub.Run(stop)

	go func() {
		for i := 0; i < 200; i++ {
			hub.Publish(live.Sample{
				Device: "ms4525",
				Values: map[string]float64{pod.ChAirspeedDP: 12},
			})
			time.Sleep(20 * time.Millisecond)
		}
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	mean, n, err := samplePitotDpAverageMin(ctx, hub, 1200*time.Millisecond, 10)
	if err != nil {
		t.Fatal(err)
	}
	if n < 10 {
		t.Fatalf("samples=%d want >= 10", n)
	}
	if math.Abs(mean-12) > 0.01 {
		t.Fatalf("mean=%v want ~12", mean)
	}
}

func TestAirspeedFromHub_publishes(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	hub := live.NewHub()
	holder := config.NewHolder("", config.Defaults())
	go AirspeedFromHub(ctx, holder, hub, nil)

	hub.Publish(live.Sample{
		Device: "ms4525",
		TsNs:   1,
		Values: map[string]float64{
			pod.ChAirspeedDP:   1621,
			pod.ChAirspeedTemp: 28,
		},
	})
	hub.Publish(live.Sample{
		Device: "bmp581",
		TsNs:   1,
		Values: map[string]float64{
			pod.ChStaticP:    101_325,
			pod.ChStaticTemp: 30,
		},
	})

	deadline := time.Now().Add(500 * time.Millisecond)
	for time.Now().Before(deadline) {
		snap := hub.SnapshotNow()
		if sm, ok := snap.Devices[airspeedDeviceName]; ok {
			if ias, ok := sm.Values["ias_kt"]; ok && ias > 90 && ias < 110 {
				if tas, ok := sm.Values["tas_kt"]; ok && tas > ias {
					return
				}
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("expected airspeed device with ias_kt ~100 and tas_kt > ias_kt")
}
