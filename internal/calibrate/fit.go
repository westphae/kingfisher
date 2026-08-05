package calibrate

import (
	"fmt"
	"math"

	"github.com/westphae/kingfisher/internal/config"
)

// G0 is standard gravity (m/s²).
const G0 = 9.80665

// FaceSample is a locked mean vector for one face or still dwell.
type FaceSample struct {
	Face     Face       `json:"face"`
	Accel    [3]float64 `json:"accel_ms2,omitempty"`
	Gyro     [3]float64 `json:"gyro_rads,omitempty"`
	Mag      [3]float64 `json:"mag_ut,omitempty"`
	TempC    float64    `json:"temp_c,omitempty"`
	Samples  int        `json:"samples"`
	Duration float64    `json:"duration_s"`
	// MotionFrac is the fraction of the lock window that failed the stillness
	// gate (0 = perfectly still). Accept is still allowed when >0.
	MotionFrac float64 `json:"motion_frac,omitempty"`
	// PeakGyroDPS is max ‖ω‖ during the lock (cabin gyro), for the review UI.
	PeakGyroDPS float64 `json:"peak_gyro_dps,omitempty"`
	// PeakAccelVar is max short-window accel variance during the lock.
	PeakAccelVar float64 `json:"peak_accel_var,omitempty"`
}

// FitAccel computes diagonal accel scale/bias from 6 faces.
func FitAccel(faces map[Face]FaceSample) (*config.IMUCalResult, error) {
	need := []Face{FacePlusX, FaceMinusX, FacePlusY, FaceMinusY, FacePlusZ, FaceMinusZ}
	for _, f := range need {
		if _, ok := faces[f]; !ok {
			return nil, fmt.Errorf("calibrate: missing face %s", f)
		}
	}
	var scale, bias [3]float64
	var warns []string
	for axis, pair := range [][2]Face{
		{FacePlusX, FaceMinusX},
		{FacePlusY, FaceMinusY},
		{FacePlusZ, FaceMinusZ},
	} {
		ap := faces[pair[0]].Accel[axis]
		an := faces[pair[1]].Accel[axis]
		bias[axis] = 0.5 * (ap + an)
		half := 0.5 * (ap - an)
		if math.Abs(half) < 1 {
			return nil, fmt.Errorf("calibrate: face pair %s/%s too close on axis %d", pair[0], pair[1], axis)
		}
		scale[axis] = G0 / half
		latP := lateralResidual(faces[pair[0]].Accel, axis)
		latN := lateralResidual(faces[pair[1]].Accel, axis)
		if latP > 2.0 || latN > 2.0 {
			warns = append(warns, fmt.Sprintf("high off-axis residual on %s/%s (%.2f / %.2f m/s²; case skew OK for diagonal k,l)", pair[0], pair[1], latP, latN))
		}
	}

	nFace := float64(len(need))
	var sumNorm, sumSq float64
	for _, f := range need {
		a := faces[f].Accel
		var corr [3]float64
		for i := 0; i < 3; i++ {
			corr[i] = scale[i] * (a[i] - bias[i])
		}
		n := math.Hypot(corr[0], math.Hypot(corr[1], corr[2]))
		sumNorm += n
		d := n - G0
		sumSq += d * d
	}
	meanN := sumNorm / nFace
	rms := math.Sqrt(sumSq / nFace)
	if math.Abs(meanN-G0)/G0 > 0.005 {
		warns = append(warns, fmt.Sprintf("mean ‖a_corr‖=%.4f m/s² is >0.5%% from g₀", meanN))
	}

	return &config.IMUCalResult{
		AccelScale:  scale,
		AccelBias:   bias,
		ResidualRMS: rms,
		MeanNormMS2: meanN,
		Warnings:    warns,
	}, nil
}

// FitGyroStill computes gyro bias from one still dwell (any orientation).
func FitGyroStill(sample FaceSample) (*config.IMUCalResult, error) {
	if sample.Samples == 0 {
		return nil, fmt.Errorf("calibrate: empty gyro still sample")
	}
	var warns []string
	if sample.MotionFrac > 0.05 {
		warns = append(warns, fmt.Sprintf(
			"motion during average: %.0f%% of window not still (peak ‖ω‖=%.2f °/s, peak accel var=%.3f) — usable in a pinch, prefer a quiet dwell",
			100*sample.MotionFrac, sample.PeakGyroDPS, sample.PeakAccelVar))
	}
	return &config.IMUCalResult{
		GyroBias: sample.Gyro,
		TempCalC: sample.TempC,
		Warnings: warns,
	}, nil
}

// FitAccelGyro is kept for tests: accel six-face + mean gyro of all faces.
func FitAccelGyro(faces map[Face]FaceSample) (*config.IMUCalResult, error) {
	fit, err := FitAccel(faces)
	if err != nil {
		return nil, err
	}
	need := []Face{FacePlusX, FaceMinusX, FacePlusY, FaceMinusY, FacePlusZ, FaceMinusZ}
	var gSum [3]float64
	var sumT float64
	for _, f := range need {
		for i := 0; i < 3; i++ {
			gSum[i] += faces[f].Gyro[i]
		}
		sumT += faces[f].TempC
	}
	nFace := float64(len(need))
	for i := 0; i < 3; i++ {
		fit.GyroBias[i] = gSum[i] / nFace
	}
	fit.TempCalC = sumT / nFace

	var gRMS [3]float64
	for i := 0; i < 3; i++ {
		var sumSq float64
		for _, f := range need {
			d := faces[f].Gyro[i] - fit.GyroBias[i]
			sumSq += d * d
		}
		gRMS[i] = math.Sqrt(sumSq / nFace)
	}
	fit.GyroFaceRMS = gRMS
	return fit, nil
}

// FitMag computes diagonal soft-iron + hard-iron from 6 mag face means.
func FitMag(faces map[Face]FaceSample) (*config.MagCalResult, error) {
	need := []Face{FacePlusX, FaceMinusX, FacePlusY, FaceMinusY, FacePlusZ, FaceMinusZ}
	for _, f := range need {
		if _, ok := faces[f]; !ok {
			return nil, fmt.Errorf("calibrate: missing face %s", f)
		}
	}
	var hard, soft [3]float64
	var warns []string
	var halfRanges [3]float64
	for axis, pair := range [][2]Face{
		{FacePlusX, FaceMinusX},
		{FacePlusY, FaceMinusY},
		{FacePlusZ, FaceMinusZ},
	} {
		ap := faces[pair[0]].Mag[axis]
		an := faces[pair[1]].Mag[axis]
		hard[axis] = 0.5 * (ap + an)
		half := 0.5 * (ap - an)
		halfRanges[axis] = math.Abs(half)
		if halfRanges[axis] < 1 {
			return nil, fmt.Errorf("calibrate: mag face pair %s/%s too close on axis %d", pair[0], pair[1], axis)
		}
	}
	geo := math.Cbrt(halfRanges[0] * halfRanges[1] * halfRanges[2])
	for i := 0; i < 3; i++ {
		soft[i] = geo / halfRanges[i]
	}

	var norms []float64
	for _, f := range need {
		m := faces[f].Mag
		var corr [3]float64
		for i := 0; i < 3; i++ {
			corr[i] = soft[i] * (m[i] - hard[i])
		}
		norms = append(norms, math.Hypot(corr[0], math.Hypot(corr[1], corr[2])))
	}
	meanN := 0.0
	for _, n := range norms {
		meanN += n
	}
	meanN /= float64(len(norms))
	sumSq := 0.0
	for _, n := range norms {
		d := n - meanN
		sumSq += d * d
	}
	rms := math.Sqrt(sumSq / float64(len(norms)))
	if rms > 2.0 { // µT
		warns = append(warns, fmt.Sprintf("‖B_corr‖ scatter RMS=%.2f µT (soft-iron/interference?)", rms))
	}

	return &config.MagCalResult{
		SoftIronDiag: soft,
		HardIron:     hard,
		ResidualRMS:  rms,
		MeanNormUT:   meanN,
		Warnings:     warns,
	}, nil
}

func lateralResidual(a [3]float64, axis int) float64 {
	var sum float64
	for i := 0; i < 3; i++ {
		if i == axis {
			continue
		}
		sum += a[i] * a[i]
	}
	return math.Sqrt(sum)
}
