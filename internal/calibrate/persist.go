package calibrate

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/sensors"
)

// Artifact is the offline JSON written under ~/kingfisher/calibration/.
type Artifact struct {
	Kind        string                `json:"kind"` // cabin_accel | cabin_gyro | cabin_imu | pod_mag
	FittedUTC   string                `json:"fitted_utc"`
	IMU         *config.IMUCalResult  `json:"imu,omitempty"`
	Mag         *config.MagCalResult  `json:"mag,omitempty"`
	FaceSamples map[string]FaceSample `json:"face_samples"`
}

// PersistAccel programs accel OFFUSER from a six-face fit and merges into cabin_imu.
func PersistAccel(holder *config.Holder, reg *sensors.Registry, fit *config.IMUCalResult, faces map[Face]FaceSample) error {
	if err := persistIMUCommon(holder, reg, fit); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := sensors.ApplyCabinAccelOffuser(ctx, reg.Gate(), reg, holder, fit); err != nil {
		return err
	}
	return writeArtifact(Artifact{
		Kind:        "cabin_accel",
		FittedUTC:   fit.FittedUTC,
		IMU:         fit,
		FaceSamples: faceMapString(faces),
	})
}

// PersistGyro programs T_ref-baked gyro OFFUSER from a still fit and merges into cabin_imu.
func PersistGyro(holder *config.Holder, reg *sensors.Registry, fit *config.IMUCalResult, faces map[Face]FaceSample) error {
	if err := persistIMUCommon(holder, reg, fit); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := sensors.ApplyCabinGyroOffuser(ctx, reg.Gate(), reg, holder, fit); err != nil {
		return err
	}
	return writeArtifact(Artifact{
		Kind:        "cabin_gyro",
		FittedUTC:   fit.FittedUTC,
		IMU:         fit,
		FaceSamples: faceMapString(faces),
	})
}

// PersistIMU programs both (legacy combined Accept).
func PersistIMU(holder *config.Holder, reg *sensors.Registry, fit *config.IMUCalResult, faces map[Face]FaceSample) error {
	if err := persistIMUCommon(holder, reg, fit); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := sensors.ApplyCabinIMUOffuser(ctx, reg.Gate(), reg, holder, fit); err != nil {
		return err
	}
	return writeArtifact(Artifact{
		Kind:        "cabin_imu",
		FittedUTC:   fit.FittedUTC,
		IMU:         fit,
		FaceSamples: faceMapString(faces),
	})
}

func persistIMUCommon(holder *config.Holder, reg *sensors.Registry, fit *config.IMUCalResult) error {
	if holder == nil {
		return fmt.Errorf("calibrate: no config holder")
	}
	if fit == nil {
		return fmt.Errorf("calibrate: nil fit")
	}
	if reg == nil {
		return fmt.Errorf("calibrate: no sensor registry (cannot program OFFUSER)")
	}
	return nil
}

// PersistMag writes pod mag cal to config and a timestamped JSON file.
func PersistMag(holder *config.Holder, fit *config.MagCalResult, faces map[Face]FaceSample) error {
	cur := holder.Get()
	cp := *cur
	cp.Calibration = cur.Calibration
	cp.Calibration.PodMag = fit
	holder.Set(&cp)
	if err := config.Save(holder.Path(), &cp); err != nil {
		return err
	}
	return writeArtifact(Artifact{
		Kind:        "pod_mag",
		FittedUTC:   fit.FittedUTC,
		Mag:         fit,
		FaceSamples: faceMapString(faces),
	})
}

func faceMapString(faces map[Face]FaceSample) map[string]FaceSample {
	out := make(map[string]FaceSample, len(faces))
	for f, sm := range faces {
		out[string(f)] = sm
	}
	return out
}

func writeArtifact(a Artifact) error {
	dir := config.DefaultCalDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("calibrate: mkdir %s: %w", dir, err)
	}
	ts := time.Now().UTC().Format("20060102T150405Z")
	path := filepath.Join(dir, fmt.Sprintf("%s_%s.json", a.Kind, ts))
	raw, err := json.MarshalIndent(a, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(raw, '\n'), 0o644)
}
