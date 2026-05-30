package derive

import (
	"math"
	"testing"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
)

func TestInTaxiBand(t *testing.T) {
	c := config.Compass{TaxiMinKt: 2, TaxiMaxKt: 40}
	kt := func(k float64) float64 { return k / mpsPerKt }
	tests := []struct {
		speed float64
		want  bool
	}{
		{kt(1.9), false},
		{kt(2), true},
		{kt(20), true},
		{kt(40), true},
		{kt(40.1), false},
	}
	for _, tc := range tests {
		fix := gps.Fix{HasFix: true, Speed: tc.speed}
		got := inTaxiBand(fix, c.TaxiMinKtOrDefault(), c.TaxiMaxKtOrDefault())
		if got != tc.want {
			t.Errorf("speed %.2f kt: got %v want %v", tc.speed*mpsPerKt, got, tc.want)
		}
	}
}

func TestPickMagExplicit(t *testing.T) {
	snap := live.Snapshot{Devices: map[string]live.Sample{
		"icm20948": {Values: map[string]float64{"magn_x": 1, "magn_y": 2, "magn_z": 3}},
		"mmc5983":  {Values: map[string]float64{"mag_x_ut": 4, "mag_y_ut": 5, "mag_z_ut": 6}},
	}}
	name, v, ok := pickMag(snap, "mmc5983")
	if !ok || name != "mmc5983" || v.X != 4 {
		t.Fatalf("pickMag explicit: %q %+v %v", name, v, ok)
	}
}

func TestPickMagAuto(t *testing.T) {
	snap := live.Snapshot{Devices: map[string]live.Sample{
		"mmc5983": {Values: map[string]float64{"mag_x_ut": 4, "mag_y_ut": 5, "mag_z_ut": 6}},
	}}
	_, v, ok := pickMag(snap, "")
	if !ok || math.Abs(v.X-4) > 1e-9 {
		t.Fatalf("pickMag auto: %+v %v", v, ok)
	}
}
