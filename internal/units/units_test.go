package units

import "testing"

func TestNormalizeIIO_pressure(t *testing.T) {
	got := NormalizeIIO("pressure", 101.325)
	want := 101_325.0
	if got != want {
		t.Fatalf("got %v want %v", got, want)
	}
}

func TestNormalizeTempC_milliC(t *testing.T) {
	got := NormalizeTempC(24_783)
	if got < 24.7 || got > 24.9 {
		t.Fatalf("got %v want ~24.78 °C", got)
	}
}

func TestNormalizeTempC_alreadyC(t *testing.T) {
	if NormalizeTempC(24.5) != 24.5 {
		t.Fatal("should not rescale °C")
	}
}

func TestNormalizeIIO_magnGaussToMicroTesla(t *testing.T) {
	got := NormalizeIIO("magn_x", 0.49)
	want := 49.0
	if got != want {
		t.Fatalf("got %v want %v", got, want)
	}
}

func TestColumnForIIO(t *testing.T) {
	if ColumnForIIO("pressure") != "pressure_pa" {
		t.Fatal("pressure column")
	}
	if ColumnForIIO("temp") != "temp_c" {
		t.Fatal("temp column")
	}
	if ColumnForIIO("accel_x") != "" {
		t.Fatal("accel unchanged")
	}
}
