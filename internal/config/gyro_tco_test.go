package config

import (
	"math"
	"testing"
)

func TestDefaultGyroTCOValid(t *testing.T) {
	g := DefaultGyroTCO()
	if !g.Valid() {
		t.Fatal("default table invalid")
	}
	if g.TRefC != DefaultGyroTCORefC {
		t.Fatalf("TRefC=%v", g.TRefC)
	}
	d := g.DeltaDPSAt(g.TRefC)
	for i, v := range d {
		if math.Abs(v) > 1e-12 {
			t.Fatalf("Δb(T_ref)[%d]=%v want 0", i, v)
		}
	}
}

func TestGyroTCOInterp(t *testing.T) {
	g := DefaultGyroTCO()
	// Midway 30→35 should be half of Δb(30).
	d30 := g.DeltaDPSAt(30)
	d325 := g.DeltaDPSAt(32.5)
	for i := 0; i < 3; i++ {
		want := 0.5 * d30[i]
		if math.Abs(d325[i]-want) > 1e-9 {
			t.Fatalf("axis %d: got %v want %v", i, d325[i], want)
		}
	}
	// Clamp below cold knee.
	dCold := g.DeltaDPSAt(-40)
	if dCold != g.DeltaDPSAt(-22) {
		t.Fatalf("cold clamp: %v vs %v", dCold, g.DeltaDPSAt(-22))
	}
}

func TestMergeGyroTCODefaults(t *testing.T) {
	var g GyroTCO
	MergeGyroTCODefaults(&g)
	if !g.Valid() || g.TRefC != DefaultGyroTCORefC {
		t.Fatalf("merge empty: %+v", g)
	}
	g2 := DefaultGyroTCO()
	g2.TRefC = 40
	MergeGyroTCODefaults(&g2)
	if g2.TRefC != 40 {
		t.Fatalf("should keep valid TRefC, got %v", g2.TRefC)
	}
}

func TestDeltaRadAt(t *testing.T) {
	g := DefaultGyroTCO()
	dps := g.DeltaDPSAt(40)
	rad := g.DeltaRadAt(40)
	s := math.Pi / 180
	for i := 0; i < 3; i++ {
		if math.Abs(rad[i]-dps[i]*s) > 1e-12 {
			t.Fatalf("axis %d", i)
		}
	}
}
