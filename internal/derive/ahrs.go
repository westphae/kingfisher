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
	"github.com/westphae/kingfisher/internal/units"
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
					"yaw":             units.Heading360(radToDeg(heading)),
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

// findIMU returns one IMU sample for attitude. A combined chip (accel and
// gyro on the same device) is used as-is. The cabin ICM-45686 publishes
// accel and gyro as two IIO devices; those are merged so a single tick sees
// both. Map iteration order is not a tie-break — equal scores pick the
// lexicographically smaller device name so the source cannot flap.
func findIMU(s live.Snapshot) (string, live.Sample, bool) {
	aName, aSm, haveA := bestIMUDevice(s, accelChannelScore)
	gName, gSm, haveG := bestIMUDevice(s, gyroChannelScore)
	switch {
	case !haveA && !haveG:
		return "", live.Sample{}, false
	case haveA && !haveG:
		return aName, aSm, true
	case !haveA && haveG:
		return gName, gSm, true
	case aName == gName:
		return aName, aSm, true
	default:
		return aName + "+" + gName, mergeSplitIMU(aSm, gSm), true
	}
}

func bestIMUDevice(s live.Snapshot, scoreFn func(string, live.Sample) int) (string, live.Sample, bool) {
	bestName := ""
	var best live.Sample
	bestScore := 0
	for name, sm := range s.Devices {
		sc := scoreFn(name, sm)
		if sc <= 0 {
			continue
		}
		if bestName == "" || sc > bestScore || (sc == bestScore && name < bestName) {
			bestScore = sc
			bestName = name
			best = sm
		}
	}
	return bestName, best, bestName != ""
}

func accelChannelScore(name string, sm live.Sample) int {
	return channelFamilyScore(name, sm, func(k string) bool {
		return strings.HasPrefix(k, "accel_") || k == "ax" || k == "ay" || k == "az"
	})
}

func gyroChannelScore(name string, sm live.Sample) int {
	return channelFamilyScore(name, sm, func(k string) bool {
		return strings.HasPrefix(k, "anglvel_") || k == "gx" || k == "gy" || k == "gz"
	})
}

func channelFamilyScore(name string, sm live.Sample, match func(string) bool) int {
	score := 0
	for k := range sm.Values {
		if match(k) {
			score += 2
		}
	}
	if score > 0 && imuFamilyName(name) {
		score++
	}
	return score
}

func imuFamilyName(name string) bool {
	low := strings.ToLower(name)
	return strings.Contains(low, "icm") || strings.Contains(low, "mpu") || strings.Contains(low, "bmi")
}

// mergeSplitIMU copies accel-device channels and fills any missing keys from
// the gyro device. Timestamp follows whichever half actually carries a
// magnetometer, so the stale-mag check in buildMeasurement still sees that
// sample's capture time.
func mergeSplitIMU(accel, gyro live.Sample) live.Sample {
	vals := make(map[string]float64, len(accel.Values)+len(gyro.Values))
	for k, v := range accel.Values {
		vals[k] = v
	}
	for k, v := range gyro.Values {
		if _, exists := vals[k]; !exists {
			vals[k] = v
		}
	}
	ts := accel.TsNs
	if _, ok := extractMag(accel.Values); !ok {
		if _, ok := extractMag(gyro.Values); ok {
			ts = gyro.TsNs
		}
	}
	return live.Sample{Device: accel.Device, TsNs: ts, Values: vals}
}
