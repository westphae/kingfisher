package calibrate

import (
	"context"
	"fmt"
	"math"
	"time"

	"github.com/westphae/kingfisher/internal/live"
)

const (
	// FaceLockDuration is how long six-face accel/mag Lock averages hub samples.
	FaceLockDuration = 8 * time.Second
	// GyroLockDuration is a longer still average for cabin gyro bias (one dwell).
	GyroLockDuration = 30 * time.Second
	minLockSamples   = 40
	minGyroSamples   = 100

	accelDevice = "icm45686-accel"
	gyroDevice  = "icm45686-gyro"
	magDevice   = "mmc5983"

	// stillAccelVarLock is the per-window variance gate during averaging
	// (same order as seek stillAccelVarMax).
	stillAccelVarLock = 0.05
	// stillGyroRateLock rad/s — ‖ω‖ above this counts as motion (~2 °/s).
	stillGyroRateLock = 2.0 * math.Pi / 180
	accelHistLock     = 25
)

// Target selects which calibration procedure is running.
type Target string

const (
	TargetCabinAccel Target = "cabin_accel"
	TargetCabinGyro  Target = "cabin_gyro"
	TargetPodMag     Target = "pod_mag"
	// TargetCabinIMU is a deprecated alias for TargetCabinAccel (old clients).
	TargetCabinIMU Target = "cabin_imu"
)

func (t Target) Valid() bool {
	switch t {
	case TargetCabinAccel, TargetCabinGyro, TargetPodMag, TargetCabinIMU:
		return true
	default:
		return false
	}
}

// Normalize maps deprecated aliases to canonical targets.
func (t Target) Normalize() Target {
	if t == TargetCabinIMU {
		return TargetCabinAccel
	}
	return t
}

// FacesNeeded is how many locked samples complete the procedure.
func (t Target) FacesNeeded() int {
	switch t.Normalize() {
	case TargetCabinGyro:
		return 1
	default:
		return len(Faces)
	}
}

// LockDuration is the averaging window for Lock.
func (t Target) LockDuration() time.Duration {
	switch t.Normalize() {
	case TargetCabinGyro:
		return GyroLockDuration
	default:
		return FaceLockDuration
	}
}

// SeekMetrics are live guidance values for the current face / dwell.
type SeekMetrics struct {
	LateralMS2    float64 `json:"lateral_ms2,omitempty"`
	Variance      float64 `json:"variance"`
	Still         bool    `json:"still"`
	Norm          float64 `json:"norm"`
	AxisPrimary   float64 `json:"axis_primary"`
	HaveSample    bool    `json:"have_sample"`
	DetectedFace  Face    `json:"detected_face,omitempty"`
	Dominance     float64 `json:"dominance,omitempty"`
	FaceOK        bool    `json:"face_ok"`
	AlreadyLocked bool    `json:"already_locked"`
}

// LockProgressLive is updated during PhaseLocking for the UI.
type LockProgressLive struct {
	MotionFrac   float64 `json:"motion_frac"`
	PeakGyroDPS  float64 `json:"peak_gyro_dps,omitempty"`
	PeakAccelVar float64 `json:"peak_accel_var,omitempty"`
	StillNow     bool    `json:"still_now"`
}

type vec3 = [3]float64

func readAccel(snap live.Snapshot) (vec3, float64, bool) {
	s, ok := snap.Devices[accelDevice]
	if !ok {
		return vec3{}, 0, false
	}
	ax, okX := s.Values["accel_x"]
	ay, okY := s.Values["accel_y"]
	az, okZ := s.Values["accel_z"]
	if !okX || !okY || !okZ {
		return vec3{}, 0, false
	}
	temp := s.Values["temp_c"]
	return vec3{ax, ay, az}, temp, true
}

func readGyro(snap live.Snapshot) (vec3, float64, bool) {
	s, ok := snap.Devices[gyroDevice]
	if !ok {
		return vec3{}, 0, false
	}
	gx, okX := s.Values["anglvel_x"]
	gy, okY := s.Values["anglvel_y"]
	gz, okZ := s.Values["anglvel_z"]
	if !okX || !okY || !okZ {
		return vec3{}, 0, false
	}
	temp := s.Values["temp_c"]
	return vec3{gx, gy, gz}, temp, true
}

func readMag(snap live.Snapshot) (vec3, bool) {
	s, ok := snap.Devices[magDevice]
	if !ok {
		return vec3{}, false
	}
	mx, okX := s.Values["mag_x_ut"]
	my, okY := s.Values["mag_y_ut"]
	mz, okZ := s.Values["mag_z_ut"]
	if !okX || !okY || !okZ {
		return vec3{}, false
	}
	return vec3{mx, my, mz}, true
}

func norm3(v vec3) float64 {
	return math.Hypot(v[0], math.Hypot(v[1], v[2]))
}

func variance3(vs []vec3) float64 {
	if len(vs) < 2 {
		return 0
	}
	var mean vec3
	for _, v := range vs {
		for i := 0; i < 3; i++ {
			mean[i] += v[i]
		}
	}
	n := float64(len(vs))
	for i := 0; i < 3; i++ {
		mean[i] /= n
	}
	var sum float64
	for _, v := range vs {
		for i := 0; i < 3; i++ {
			d := v[i] - mean[i]
			sum += d * d
		}
	}
	return sum / (n * 3)
}

// averageFace collects hub samples for duration and returns means.
// progress, if non-nil, is updated during the window for the UI.
func averageFace(ctx context.Context, hub *live.Hub, target Target, face Face, duration time.Duration, progress *LockProgressLive) (FaceSample, error) {
	if hub == nil {
		return FaceSample{}, fmt.Errorf("calibrate: no hub")
	}
	ch := hub.Subscribe()
	defer hub.Unsubscribe(ch)

	var sumA, sumG, sumM vec3
	var sumT float64
	var nA, nG, nM, nT int
	var accelHist []vec3
	var motionN, gateN int
	var peakGyro, peakAVar float64

	deadline := time.Now().Add(duration)
	target = target.Normalize()
	for time.Now().Before(deadline) {
		remaining := time.Until(deadline)
		select {
		case <-ctx.Done():
			return FaceSample{}, ctx.Err()
		case snap, ok := <-ch:
			if !ok {
				return FaceSample{}, fmt.Errorf("calibrate: hub closed")
			}
			a, aTemp, aOK := readAccel(snap)
			g, gTemp, gOK := readGyro(snap)
			if aOK {
				accelHist = append(accelHist, a)
				if len(accelHist) > accelHistLock {
					accelHist = accelHist[len(accelHist)-accelHistLock:]
				}
			}
			aVar := variance3(accelHist)
			if aVar > peakAVar {
				peakAVar = aVar
			}
			gRate := 0.0
			if gOK {
				gRate = norm3(g)
				if gRate > peakGyro {
					peakGyro = gRate
				}
			}

			moving := false
			switch target {
			case TargetCabinAccel, TargetCabinGyro:
				if len(accelHist) >= 5 && aVar > stillAccelVarLock {
					moving = true
				}
				if target == TargetCabinGyro && gOK && gRate > stillGyroRateLock {
					moving = true
				}
			case TargetPodMag:
				// Mag lock: use accel if present, else no motion flag.
				if len(accelHist) >= 5 && aVar > stillAccelVarLock {
					moving = true
				}
			}
			gateN++
			if moving {
				motionN++
			}
			if progress != nil {
				progress.MotionFrac = float64(motionN) / float64(gateN)
				progress.PeakGyroDPS = peakGyro * 180 / math.Pi
				progress.PeakAccelVar = peakAVar
				progress.StillNow = !moving
			}

			switch target {
			case TargetCabinAccel:
				if aOK {
					for i := 0; i < 3; i++ {
						sumA[i] += a[i]
					}
					sumT += aTemp
					nT++
					nA++
				}
			case TargetCabinGyro:
				if gOK {
					for i := 0; i < 3; i++ {
						sumG[i] += g[i]
					}
					nG++
					sumT += gTemp
					nT++
				}
			case TargetPodMag:
				if m, ok := readMag(snap); ok {
					for i := 0; i < 3; i++ {
						sumM[i] += m[i]
					}
					nM++
				}
			}
		case <-time.After(remaining):
		}
	}

	out := FaceSample{
		Face:         face,
		Duration:     duration.Seconds(),
		PeakGyroDPS:  peakGyro * 180 / math.Pi,
		PeakAccelVar: peakAVar,
	}
	if gateN > 0 {
		out.MotionFrac = float64(motionN) / float64(gateN)
	}
	switch target {
	case TargetCabinAccel:
		if nA < minLockSamples {
			return FaceSample{}, fmt.Errorf("calibrate: only %d accel samples (need %d); is %s publishing?", nA, minLockSamples, accelDevice)
		}
		for i := 0; i < 3; i++ {
			out.Accel[i] = sumA[i] / float64(nA)
		}
		if nT > 0 {
			out.TempC = sumT / float64(nT)
		}
		out.Samples = nA
	case TargetCabinGyro:
		if nG < minGyroSamples {
			return FaceSample{}, fmt.Errorf("calibrate: only %d gyro samples (need %d); is %s publishing?", nG, minGyroSamples, gyroDevice)
		}
		for i := 0; i < 3; i++ {
			out.Gyro[i] = sumG[i] / float64(nG)
		}
		if nT > 0 {
			out.TempC = sumT / float64(nT)
		}
		out.Samples = nG
	case TargetPodMag:
		if nM < minLockSamples {
			return FaceSample{}, fmt.Errorf("calibrate: only %d mag samples (need %d); is pod/%s linked?", nM, minLockSamples, magDevice)
		}
		for i := 0; i < 3; i++ {
			out.Mag[i] = sumM[i] / float64(nM)
		}
		out.Samples = nM
	}
	return out, nil
}
