package derive

import (
	"context"
	"log"
	"math"
	"strings"
	"time"

	"github.com/westphae/goflying/ahrs"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/store"
)

// ahrsMagMaxAgeNs is how stale a magnetometer sample may be before AHRS
// stops fusing it. Without this, a dropped pod link (frozen pod-mag sample
// in the hub, which never evicts) would silently lock the fused heading to
// a stale magnetic reference while accel/gyro keep updating.
const ahrsMagMaxAgeNs = int64(2 * time.Second)

// AHRSFromHub builds AHRS Measurements from the latest IMU + GPS samples at
// rateHz and publishes attitude on the "ahrs" virtual device. Angles are
// degrees (roll, pitch, yaw from mag+IMU fusion — not a raw sensor channel).
// The simple AHRS implementation is chosen because it has the smallest
// dependency surface; the Kalman variants can be swapped in later.
func AHRSFromHub(ctx context.Context, holder *config.Holder, rateHz float64, hub *live.Hub, gpsc *gps.Client, buf *store.Buffer) {
	if rateHz <= 0 {
		rateHz = 20
	}
	provider := ahrs.NewSimpleAHRS()
	interval := time.Duration(float64(time.Second) / rateHz)
	t := time.NewTicker(interval)
	defer t.Stop()
	var lastMagSrc string
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			var magCfg *config.Compass
			if holder != nil {
				c := holder.Get()
				magCfg = &c.Compass
			}
			m, magSrc := buildMeasurement(hub.SnapshotNow(), gpsc.LastFix(), magCfg)
			if magSrc != lastMagSrc {
				if magSrc == "" {
					log.Print("ahrs: no mag source — running attitude-only")
				} else {
					log.Printf("ahrs: mag source = %s", magSrc)
				}
				lastMagSrc = magSrc
			}
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
				// TsNs is compute time, not the IMU sample time. See
				// docs/timestamps.md — use source device rows for tight
				// kinematic fusion offline.
				TsNs: time.Now().UnixNano(),
				Values: map[string]float64{
					"roll":            radToDeg(roll),
					"pitch":           radToDeg(pitch),
					"yaw":             radToDeg(heading),
					"slip_skid":       provider.SlipSkid(),
					"turn_rate_deg_s": provider.RateOfTurn(),
					"g_load":          provider.GLoad(),
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
// neither is available. magSrc names the device whose magnetometer was
// used (empty if none was found) so callers can log the active wiring.
// magCfg supplies the sensor→fuselage mount rotation for a cross-device
// (pod) magnetometer; may be nil.
func buildMeasurement(snap live.Snapshot, fix gps.Fix, magCfg *config.Compass) (*ahrs.Measurement, string) {
	m := ahrs.NewMeasurement()
	m.T = float64(snap.ServerTsNs) / 1e9

	var magSrc string
	imuName, imu, haveIMU := findIMU(snap)
	if haveIMU {
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
		// Magnetometer. First try the IMU device itself (single-chip
		// IMU+mag): it is already in the IMU/body frame, so it is used
		// raw — applying a mount rotation here would desync it from the
		// co-located accel/gyro. Otherwise fall back to any mag-bearing
		// device (cabin IMU + pod MMC5983 split) and bring it into the
		// fuselage frame with the SAME mount/Z-inversion correction the
		// compass path uses; fusing a raw pod-axis mag against cabin-frame
		// accel/gyro yields a wrong heading. Either source is rejected if
		// its sample is stale (a frozen pod-mag must not lock the heading).
		if v, ok := extractMag(imu.Values); ok {
			if snap.ServerTsNs-imu.TsNs <= ahrsMagMaxAgeNs {
				m.M1, m.M2, m.M3 = v.X, v.Y, v.Z
				m.MValid = true
				magSrc = imuName
			} else {
				magSrc = imuName + " (stale)"
			}
		} else if name, v, ok := pickMag(snap, ""); ok {
			magTsNs := int64(0)
			if msm, ok := snap.Devices[name]; ok {
				magTsNs = msm.TsNs
			}
			if snap.ServerTsNs-magTsNs <= ahrsMagMaxAgeNs {
				cv := applySensorMount(magCfg, name, v)
				m.M1, m.M2, m.M3 = cv.X, cv.Y, cv.Z
				m.MValid = true
				magSrc = name
			} else {
				magSrc = name + " (stale)"
			}
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
	if !haveIMU && !m.WValid {
		return nil, magSrc
	}
	return m, magSrc
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
// prefix. Returns the chosen device name alongside its sample so callers
// can log the active wiring or skip self-matches.
func findIMU(s live.Snapshot) (string, live.Sample, bool) {
	var best live.Sample
	bestName := ""
	bestScore := 0
	for name, sm := range s.Devices {
		score := imuScore(name, sm)
		if score > bestScore {
			bestScore = score
			bestName = name
			best = sm
		}
	}
	return bestName, best, bestScore > 0
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
