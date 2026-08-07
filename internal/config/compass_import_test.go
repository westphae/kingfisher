package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSeedPodMagDisplayCalFromCompass(t *testing.T) {
	c := Defaults()
	c.Compass.Calibration.K = []float64{1.1, 1.0, 0.9}
	c.Compass.Calibration.L = []float64{-16, 2, 28}
	if !SeedPodMagDisplayCal(c) {
		t.Fatal("expected seed")
	}
	if c.Calibration.PodMag == nil {
		t.Fatal("nil PodMag")
	}
	if c.Calibration.PodMag.SoftIronDiag[0] != 1.1 || c.Calibration.PodMag.HardIron[2] != 28 {
		t.Fatalf("got %#v", c.Calibration.PodMag)
	}
	if SeedPodMagDisplayCal(c) {
		t.Fatal("second seed should be no-op")
	}
}

func TestSeedPodMagDisplayCalFromBestFitFile(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", dir)
	magDir := filepath.Join(dir, "magkal")
	if err := os.MkdirAll(magDir, 0o755); err != nil {
		t.Fatal(err)
	}
	best := `{
		"n": 3,
		"k": [1.2, 1.1, 1.0],
		"l": [1, 2, 3],
		"p": [[0,0,0,0,0,0],[0,0,0,0,0,0],[0,0,0,0,0,0],[0,0,0,0,0,0],[0,0,0,0,0,0],[0,0,0,0,0,0]],
		"savedAt": "2026-05-20T00:00:00Z"
	}`
	if err := os.WriteFile(filepath.Join(magDir, "best_fit.json"), []byte(best), 0o644); err != nil {
		t.Fatal(err)
	}
	c := Defaults()
	// Empty compass cal → fall back to best_fit.json
	c.Compass.Calibration = CompassCalibration{}
	if !SeedPodMagDisplayCal(c) {
		t.Fatal("expected seed from best_fit")
	}
	if c.Calibration.PodMag.FittedUTC != "2026-05-20T00:00:00Z" {
		t.Fatalf("fitted %q", c.Calibration.PodMag.FittedUTC)
	}
	if c.Calibration.PodMag.SoftIronDiag[0] != 1.2 {
		t.Fatalf("k0=%v", c.Calibration.PodMag.SoftIronDiag[0])
	}
}
