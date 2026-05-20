package derive

import (
	"math"
	"testing"

	"github.com/westphae/goflying/sensors/bmp280"
)

// At standard sea-level pressure (1013.25 hPa) CalcAltitude should yield
// approximately 0 ft. The exponent uses the standard ISA gradient so 1 hPa
// of error maps to ~25 ft.
func TestPressureAltitudeAtSeaLevelIsZero(t *testing.T) {
	got := bmp280.CalcAltitude(1013.25)
	if math.Abs(got) > 1.0 {
		t.Errorf("CalcAltitude(1013.25)=%.3f ft, want ~0", got)
	}
}

func TestPressureAltitudeAt500hPaIsAround18000Ft(t *testing.T) {
	// 500 hPa is ≈ 18,000 ft in the standard atmosphere.
	got := bmp280.CalcAltitude(500.0)
	if got < 17500 || got > 18500 {
		t.Errorf("CalcAltitude(500)=%.0f ft, want ~18000", got)
	}
}
