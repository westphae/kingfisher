package config

import (
	"encoding/json"
	"log"
	"math"
	"os"
	"path/filepath"
)

type magkalBestFit struct {
	N       int         `json:"n"`
	K       []float64   `json:"k"`
	L       []float64   `json:"l"`
	P       [][]float64 `json:"p"`
	SavedAt string      `json:"savedAt"`
}

func loadMagkalBestFit() (*magkalBestFit, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(filepath.Join(dir, "magkal", "best_fit.json"))
	if err != nil {
		return nil, err
	}
	var best magkalBestFit
	if err := json.Unmarshal(data, &best); err != nil {
		return nil, err
	}
	if best.N != 3 || len(best.K) != 3 || len(best.L) != 3 {
		return nil, os.ErrInvalid
	}
	return &best, nil
}

// ImportMagkalBestFit copies k/l/P from ~/.config/magkal/best_fit.json into c when
// calibration is empty. No-op if the file is missing or incompatible.
func ImportMagkalBestFit(c *Compass) {
	if c == nil || len(c.Calibration.K) == 3 {
		return
	}
	best, err := loadMagkalBestFit()
	if err != nil {
		return
	}
	if len(best.P) != 6 {
		return
	}
	c.Calibration.K = append([]float64(nil), best.K...)
	c.Calibration.L = append([]float64(nil), best.L...)
	c.Calibration.P = cloneMatrix(best.P)
}

// SeedPodMagDisplayCal fills calibration.pod_mag from compass.calibration k/l
// (or magkal best_fit.json) when empty, so the mmc5983 tab can show raw/cal
// dual columns like cabin accel. Returns true if PodMag was set.
func SeedPodMagDisplayCal(c *Config) bool {
	if c == nil || c.Calibration.PodMag != nil {
		return false
	}
	var k, l [3]float64
	var fitted string
	if len(c.Compass.Calibration.K) == 3 && len(c.Compass.Calibration.L) == 3 {
		copy(k[:], c.Compass.Calibration.K)
		copy(l[:], c.Compass.Calibration.L)
	} else {
		best, err := loadMagkalBestFit()
		if err != nil {
			return false
		}
		copy(k[:], best.K)
		copy(l[:], best.L)
		fitted = best.SavedAt
	}
	if fitted == "" {
		fitted = "magkal-import"
	}
	c.Calibration.PodMag = &MagCalResult{
		SoftIronDiag: k,
		HardIron:     l,
		FittedUTC:    fitted,
	}
	log.Printf("config: seeded calibration.pod_mag from magkal (display soft-iron/hard-iron)")
	return true
}

func cloneMatrix(p [][]float64) [][]float64 {
	out := make([][]float64, len(p))
	for i, row := range p {
		out[i] = append([]float64(nil), row...)
	}
	return out
}

// MergeCompassKalman fills zero kalman fields from defaults.
func MergeCompassKalman(k *CompassKalman) {
	def := CompassKalmanDefaults()
	if k.SigmaK0 == 0 {
		k.SigmaK0 = def.SigmaK0
	}
	if k.SigmaK == 0 {
		k.SigmaK = def.SigmaK
	}
	if k.SigmaM == 0 {
		k.SigmaM = def.SigmaM
	}
	if k.MaxSigmaK == 0 {
		k.MaxSigmaK = def.MaxSigmaK
	}
	if k.MaxSigmaL == 0 {
		k.MaxSigmaL = def.MaxSigmaL
	}
	sm := &k.StateMachine
	dsm := def.StateMachine
	if sm.LockHysteresis == 0 {
		sm.LockHysteresis = dsm.LockHysteresis
	}
	if sm.NISWindow == 0 {
		sm.NISWindow = dsm.NISWindow
	}
	if sm.NISThreshold == 0 {
		sm.NISThreshold = dsm.NISThreshold
	}
}

func isValidRotApprox(R [3][3]float64) bool {
	for i := 0; i < 3; i++ {
		n := 0.0
		for j := 0; j < 3; j++ {
			n += R[i][j] * R[i][j]
		}
		if math.Abs(n-1) > 1e-2 {
			return false
		}
	}
	return true
}

func identityRot() [3][3]float64 {
	return [3][3]float64{{1, 0, 0}, {0, 1, 0}, {0, 0, 1}}
}

// defaultMMC5983Mount is provisional per project note: identity with z inversion.
func defaultMMC5983Mount() [3][3]float64 {
	return [3][3]float64{{1, 0, 0}, {0, 1, 0}, {0, 0, -1}}
}

func fallbackSensorMount(name string) [3][3]float64 {
	if len(name) >= 7 && name[:7] == "mmc5983" {
		return defaultMMC5983Mount()
	}
	return identityRot()
}

func isZeroRot(R [3][3]float64) bool {
	for i := 0; i < 3; i++ {
		for j := 0; j < 3; j++ {
			if R[i][j] != 0 {
				return false
			}
		}
	}
	return true
}

// MigrateCompassMounts keeps backwards compatibility with legacy pod_mount_r and
// guarantees a map exists for sensor mount overrides.
func MigrateCompassMounts(c *Config) {
	if c == nil {
		return
	}
	if c.Compass.SensorMountR == nil {
		c.Compass.SensorMountR = map[string][3][3]float64{}
	}
	if !isZeroRot(c.Compass.PodMountR) && c.Compass.Magnetometer != "" {
		if _, exists := c.Compass.SensorMountR[c.Compass.Magnetometer]; !exists {
			c.Compass.SensorMountR[c.Compass.Magnetometer] = c.Compass.PodMountR
			log.Printf("config: migrated compass.pod_mount_r into compass.sensor_mount_r[%q]", c.Compass.Magnetometer)
		}
	}
	for name, R := range c.Compass.SensorMountR {
		if !isValidRotApprox(R) {
			log.Printf("config: invalid compass.sensor_mount_r[%q], using fallback", name)
			c.Compass.SensorMountR[name] = fallbackSensorMount(name)
		}
	}
	if c.Compass.Magnetometer != "" {
		if _, ok := c.Compass.SensorMountR[c.Compass.Magnetometer]; !ok {
			c.Compass.SensorMountR[c.Compass.Magnetometer] = fallbackSensorMount(c.Compass.Magnetometer)
		}
	}
}
