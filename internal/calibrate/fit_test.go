package calibrate

import (
	"math"
	"testing"
)

func TestFitAccelGyroIdeal(t *testing.T) {
	faces := map[Face]FaceSample{}
	// Ideal: scale 1, bias 0, g along each axis.
	mk := func(f Face, a [3]float64) {
		faces[f] = FaceSample{Face: f, Accel: a, Gyro: [3]float64{0.01, -0.02, 0.03}, TempC: 34.5, Samples: 100}
	}
	mk(FacePlusX, [3]float64{G0, 0, 0})
	mk(FaceMinusX, [3]float64{-G0, 0, 0})
	mk(FacePlusY, [3]float64{0, G0, 0})
	mk(FaceMinusY, [3]float64{0, -G0, 0})
	mk(FacePlusZ, [3]float64{0, 0, G0})
	mk(FaceMinusZ, [3]float64{0, 0, -G0})

	fit, err := FitAccelGyro(faces)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if math.Abs(fit.AccelScale[i]-1) > 1e-6 {
			t.Errorf("scale[%d]=%v", i, fit.AccelScale[i])
		}
		if math.Abs(fit.AccelBias[i]) > 1e-9 {
			t.Errorf("bias[%d]=%v", i, fit.AccelBias[i])
		}
		if math.Abs(fit.GyroBias[i]-faces[FacePlusX].Gyro[i]) > 1e-9 {
			t.Errorf("gyroBias[%d]=%v", i, fit.GyroBias[i])
		}
	}
	if math.Abs(fit.MeanNormMS2-G0) > 1e-6 {
		t.Errorf("mean norm %v", fit.MeanNormMS2)
	}
	for i := 0; i < 3; i++ {
		if fit.GyroFaceRMS[i] > 1e-12 {
			t.Errorf("gyroFaceRMS[%d]=%v want ~0", i, fit.GyroFaceRMS[i])
		}
	}
	if len(fit.Warnings) != 0 {
		t.Errorf("unexpected warnings: %v", fit.Warnings)
	}
	if math.Abs(fit.TempCalC-34.5) > 1e-9 {
		t.Errorf("TempCalC=%v want 34.5", fit.TempCalC)
	}
}

func TestFitGyroStill(t *testing.T) {
	sm := FaceSample{
		Face:    FaceStill,
		Gyro:    [3]float64{0.01, -0.02, 0.03},
		TempC:   35.2,
		Samples: 200,
	}
	fit, err := FitGyroStill(sm)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if fit.GyroBias[i] != sm.Gyro[i] {
			t.Errorf("gyro[%d]=%v", i, fit.GyroBias[i])
		}
	}
	if fit.TempCalC != 35.2 {
		t.Errorf("TempCalC=%v", fit.TempCalC)
	}
}

func TestFitAccelGyroFaceRMS(t *testing.T) {
	faces := map[Face]FaceSample{}
	mk := func(f Face, a [3]float64, g [3]float64) {
		faces[f] = FaceSample{Face: f, Accel: a, Gyro: g, Samples: 50}
	}
	base := [3]float64{0.01, -0.02, 0.03}
	mk(FacePlusX, [3]float64{G0, 0, 0}, base)
	mk(FaceMinusX, [3]float64{-G0, 0, 0}, base)
	mk(FacePlusY, [3]float64{0, G0, 0}, base)
	mk(FaceMinusY, [3]float64{0, -G0, 0}, base)
	mk(FacePlusZ, [3]float64{0, 0, G0}, base)
	outlier := base
	outlier[0] = 0.05
	mk(FaceMinusZ, [3]float64{0, 0, -G0}, outlier)

	fit, err := FitAccelGyro(faces)
	if err != nil {
		t.Fatal(err)
	}
	if fit.GyroFaceRMS[0] < 0.01 {
		t.Fatalf("expected measurable gyro X RMS, got %v", fit.GyroFaceRMS[0])
	}
}

func TestFitAccelGyroScaleBias(t *testing.T) {
	// True: S=(1.02,0.98,1.01), b=(0.1,-0.2,0.05)
	S := [3]float64{1.02, 0.98, 1.01}
	b := [3]float64{0.1, -0.2, 0.05}
	// a_raw = a_true/S + b  where a_true = ±g ê
	faces := map[Face]FaceSample{}
	set := func(f Face, trueAxis int, sign float64) {
		var a [3]float64
		for i := 0; i < 3; i++ {
			a[i] = b[i]
		}
		a[trueAxis] = sign*G0/S[trueAxis] + b[trueAxis]
		faces[f] = FaceSample{Face: f, Accel: a, Samples: 50}
	}
	set(FacePlusX, 0, 1)
	set(FaceMinusX, 0, -1)
	set(FacePlusY, 1, 1)
	set(FaceMinusY, 1, -1)
	set(FacePlusZ, 2, 1)
	set(FaceMinusZ, 2, -1)

	fit, err := FitAccelGyro(faces)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if math.Abs(fit.AccelScale[i]-S[i]) > 1e-6 {
			t.Errorf("scale[%d] got %v want %v", i, fit.AccelScale[i], S[i])
		}
		if math.Abs(fit.AccelBias[i]-b[i]) > 1e-6 {
			t.Errorf("bias[%d] got %v want %v", i, fit.AccelBias[i], b[i])
		}
	}
}

func TestFitMag(t *testing.T) {
	faces := map[Face]FaceSample{}
	// Hard iron (10, -5, 2), soft diag (1.1, 0.9, 1.0), field radius 50 µT
	H := [3]float64{10, -5, 2}
	L := [3]float64{1.1, 0.9, 1.0}
	R := 50.0
	mk := func(f Face, axis int, sign float64) {
		var m [3]float64
		for i := 0; i < 3; i++ {
			m[i] = H[i]
		}
		// B_corr = L*(B_raw - H) = sign*R ê  => B_raw = H + (sign*R)/L
		m[axis] = H[axis] + sign*R/L[axis]
		faces[f] = FaceSample{Face: f, Mag: m, Samples: 50}
	}
	mk(FacePlusX, 0, 1)
	mk(FaceMinusX, 0, -1)
	mk(FacePlusY, 1, 1)
	mk(FaceMinusY, 1, -1)
	mk(FacePlusZ, 2, 1)
	mk(FaceMinusZ, 2, -1)

	fit, err := FitMag(faces)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if math.Abs(fit.HardIron[i]-H[i]) > 1e-6 {
			t.Errorf("hard[%d] got %v want %v", i, fit.HardIron[i], H[i])
		}
	}
	// Soft-iron is relative (geo-mean normalization); ratios should match.
	r01 := fit.SoftIronDiag[0] / fit.SoftIronDiag[1]
	want01 := L[0] / L[1]
	if math.Abs(r01-want01) > 1e-6 {
		t.Errorf("soft ratio 0/1 got %v want %v", r01, want01)
	}
}
