package sensors

import (
	"math"
	"testing"

	"github.com/westphae/kingfisher/internal/config"
)

func TestGyroStillMeanAtRefAtTRef(t *testing.T) {
	tco := config.DefaultGyroTCO()
	mu := [3]float64{0.01, -0.02, 0.03}
	got := GyroStillMeanAtRef(tco, mu, tco.TRefC)
	for i := 0; i < 3; i++ {
		if math.Abs(got[i]-mu[i]) > 1e-12 {
			t.Fatalf("at T_ref: [%d] got %v want %v", i, got[i], mu[i])
		}
	}
}

func TestGyroStillMeanAtRefBakesDelta(t *testing.T) {
	tco := config.DefaultGyroTCO()
	mu := [3]float64{0.1, 0.2, 0.3}
	tCal := 30.0
	d := tco.DeltaRadAt(tCal)
	got := GyroStillMeanAtRef(tco, mu, tCal)
	for i := 0; i < 3; i++ {
		want := mu[i] - d[i]
		if math.Abs(got[i]-want) > 1e-12 {
			t.Fatalf("axis %d: got %v want %v (Δ=%v)", i, got[i], want, d[i])
		}
	}
	// OFFUSER update: old − μ_ref = old − μ + Δb(T_cal)
	old := 0.05
	off := NewOffuserFromMean(old, got[0])
	wantOff := old - mu[0] + d[0]
	if math.Abs(off-wantOff) > 1e-12 {
		t.Fatalf("offuser bake: got %v want %v", off, wantOff)
	}
}
