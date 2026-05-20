package derive

import (
	"context"
	"math"
	"strings"
	"time"

	"github.com/westphae/goflying/ahrs"

	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/store"
)

// AHRSFromHub builds AHRS Measurements from the latest IMU + GPS samples at
// rateHz and publishes roll/pitch/heading on the "ahrs" virtual device.
// The simple AHRS implementation is chosen because it has the smallest
// dependency surface; the Kalman variants can be swapped in later.
func AHRSFromHub(ctx context.Context, rateHz float64, hub *live.Hub, gpsc *gps.Client, buf *store.Buffer) {
	if rateHz <= 0 {
		rateHz = 20
	}
	provider := ahrs.NewSimpleAHRS()
	interval := time.Duration(float64(time.Second) / rateHz)
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			m := buildMeasurement(hub.SnapshotNow(), gpsc.LastFix())
			if m == nil {
				continue
			}
			provider.Compute(m)
			if !provider.Valid() {
				continue
			}
			roll, pitch, heading := provider.RollPitchHeading()
			sm := live.Sample{
				Device: "ahrs",
				TsNs:   time.Now().UnixNano(),
				Values: map[string]float64{
					"roll_deg":    radToDeg(roll),
					"pitch_deg":   radToDeg(pitch),
					"heading_deg": radToDeg(heading),
					"slip_skid":   provider.SlipSkid(),
					"turn_rate":   provider.RateOfTurn(),
					"gload":       provider.GLoad(),
				},
			}
			hub.Publish(sm)
			if buf != nil {
				buf.Append(sm)
			}
		}
	}
}

func radToDeg(r float64) float64 { return r * 180.0 / math.Pi }

// buildMeasurement looks for an IMU-flavoured sample in the snapshot
// (anything with accel + gyro channels) and a GPS fix. Returns nil if
// neither is available.
func buildMeasurement(snap live.Snapshot, fix gps.Fix) *ahrs.Measurement {
	m := ahrs.NewMeasurement()
	m.T = float64(snap.ServerTsNs) / 1e9

	if imu, ok := findIMU(snap); ok {
		ax, hasAx := imu.Values["accel_x"]
		ay, hasAy := imu.Values["accel_y"]
		az, hasAz := imu.Values["accel_z"]
		gx, hasGx := imu.Values["anglvel_x"]
		gy, hasGy := imu.Values["anglvel_y"]
		gz, hasGz := imu.Values["anglvel_z"]
		// Accept user column overrides too.
		ax, hasAx = firstFloat(imu.Values, ax, hasAx, "ax")
		ay, hasAy = firstFloat(imu.Values, ay, hasAy, "ay")
		az, hasAz = firstFloat(imu.Values, az, hasAz, "az")
		gx, hasGx = firstFloat(imu.Values, gx, hasGx, "gx")
		gy, hasGy = firstFloat(imu.Values, gy, hasGy, "gy")
		gz, hasGz = firstFloat(imu.Values, gz, hasGz, "gz")
		if hasAx && hasAy && hasAz {
			// IIO accelerometer reports m/s^2; AHRS Measurement wants G.
			m.A1 = ax / 9.80665
			m.A2 = ay / 9.80665
			m.A3 = az / 9.80665
		}
		if hasGx && hasGy && hasGz {
			// IIO gyro reports rad/s; AHRS Measurement wants deg/s.
			m.B1 = radToDeg(gx)
			m.B2 = radToDeg(gy)
			m.B3 = radToDeg(gz)
		}
		// Magnetometer (optional)
		mx, hasMx := imu.Values["magn_x"]
		my, hasMy := imu.Values["magn_y"]
		mz, hasMz := imu.Values["magn_z"]
		if hasMx && hasMy && hasMz {
			m.M1, m.M2, m.M3 = mx, my, mz
			m.MValid = true
		}
	}

	if fix.HasFix {
		hdgRad := degToRad(fix.Track)
		// W1 east, W2 north, W3 up — speed in knots
		gsKt := fix.Speed * 1.94384
		m.W1 = gsKt * math.Sin(hdgRad)
		m.W2 = gsKt * math.Cos(hdgRad)
		m.W3 = fix.Climb * 1.94384
		m.WValid = true
		m.TW = float64(fix.Time.UnixNano()) / 1e9
		if m.TW == 0 {
			m.TW = m.T
		}
	}
	return m
}

func firstFloat(vals map[string]float64, existing float64, has bool, keys ...string) (float64, bool) {
	if has {
		return existing, true
	}
	for _, k := range keys {
		if v, ok := vals[k]; ok && !math.IsNaN(v) {
			return v, true
		}
	}
	return 0, false
}

func degToRad(d float64) float64 { return d * math.Pi / 180.0 }

// findIMU picks the first device whose values look like an IMU (has any
// accel_* or anglvel_* channel), preferring devices with a known IMU name
// prefix.
func findIMU(s live.Snapshot) (live.Sample, bool) {
	var best live.Sample
	bestScore := 0
	for name, sm := range s.Devices {
		score := imuScore(name, sm)
		if score > bestScore {
			bestScore = score
			best = sm
		}
	}
	return best, bestScore > 0
}

func imuScore(name string, sm live.Sample) int {
	score := 0
	for k := range sm.Values {
		if strings.HasPrefix(k, "accel_") || strings.HasPrefix(k, "anglvel_") {
			score += 2
		}
		if k == "ax" || k == "ay" || k == "az" || k == "gx" || k == "gy" || k == "gz" {
			score += 2
		}
	}
	low := strings.ToLower(name)
	if strings.Contains(low, "icm") || strings.Contains(low, "mpu") || strings.Contains(low, "bmi") {
		score += 1
	}
	return score
}
