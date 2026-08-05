package sensors

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/westphae/kingfisher/internal/config"
)

// NewOffuserFromMean returns the OFFUSER value that zeros a still reading.
// IIO/ICM45686 treat calibbias as an additive trim (out ≈ intrinsic + OFFUSER),
// so when the published still mean is μ under the current trim:
//
//	new_OFFUSER = old_OFFUSER − μ
func NewOffuserFromMean(oldOffuser, stillMean float64) float64 {
	return oldOffuser - stillMean
}

// GyroStillMeanAtRef converts a still mean measured at T_cal into the mean that
// should be nulled onto OFFUSER so the chip is correct at T_ref:
//
//	μ_ref = μ_cal − Δb(T_cal)
//
// with Δb(T_ref) = 0 by construction. Units: rad/s.
func GyroStillMeanAtRef(tco config.GyroTCO, gyroBiasAtCal [3]float64, tempCalC float64) [3]float64 {
	t := tempCalC
	if t == 0 {
		t = tco.TRefC
	}
	if t == 0 {
		t = config.DefaultGyroTCORefC
	}
	d := tco.DeltaRadAt(t)
	var out [3]float64
	for i := 0; i < 3; i++ {
		out[i] = gyroBiasAtCal[i] - d[i]
	}
	return out
}

// ApplyCabinAccelOffuser programs accel calibbias from a six-face fit and merges
// accel fields into calibration.cabin_imu (preserving gyro cal).
func ApplyCabinAccelOffuser(ctx context.Context, gate *BufferGate, reg *Registry, holder *config.Holder, fit *config.IMUCalResult) error {
	return applyCabinOffuser(ctx, gate, reg, holder, fit, true, false)
}

// ApplyCabinGyroOffuser programs T_ref-baked gyro calibbias from a still fit and
// merges gyro fields into calibration.cabin_imu (preserving accel cal).
func ApplyCabinGyroOffuser(ctx context.Context, gate *BufferGate, reg *Registry, holder *config.Holder, fit *config.IMUCalResult) error {
	return applyCabinOffuser(ctx, gate, reg, holder, fit, false, true)
}

// ApplyCabinIMUOffuser programs both accel and gyro (legacy combined Accept).
func ApplyCabinIMUOffuser(ctx context.Context, gate *BufferGate, reg *Registry, holder *config.Holder, fit *config.IMUCalResult) error {
	return applyCabinOffuser(ctx, gate, reg, holder, fit, true, true)
}

func applyCabinOffuser(ctx context.Context, gate *BufferGate, reg *Registry, holder *config.Holder, fit *config.IMUCalResult, doAccel, doGyro bool) error {
	if fit == nil {
		return fmt.Errorf("sensors: nil IMU cal fit")
	}
	if reg == nil {
		return fmt.Errorf("sensors: nil registry")
	}
	if holder == nil {
		return fmt.Errorf("sensors: nil config holder")
	}
	if !doAccel && !doGyro {
		return fmt.Errorf("sensors: nothing to program")
	}
	return gate.WithPaused(ctx, CabinIMUPair(), func() error {
		cur := holder.Get()
		tco := cur.Calibration.GyroTCO
		config.MergeGyroTCODefaults(&tco)

		merged := mergeIMUCal(cur.Calibration.CabinIMU, fit, doAccel, doGyro)

		var gyroOff, accelOff [3]float64
		if doGyro {
			gyroAtRef := GyroStillMeanAtRef(tco, merged.GyroBias, merged.TempCalC)
			merged.GyroBiasAtRef = gyroAtRef
			for i, axis := range []string{"x", "y", "z"} {
				oldG, err := readCalibbias(reg, CabinIMUGyro, "anglvel_"+axis)
				if err != nil {
					return err
				}
				gyroOff[i] = NewOffuserFromMean(oldG, gyroAtRef[i])
				if err := writeCalibbias(reg, CabinIMUGyro, "anglvel_"+axis, gyroOff[i]); err != nil {
					return err
				}
			}
			merged.GyroOffuser = gyroOff
			merged.GyroOffuserApplied = true
		}
		if doAccel {
			for i, axis := range []string{"x", "y", "z"} {
				oldA, err := readCalibbias(reg, CabinIMUAccel, "accel_"+axis)
				if err != nil {
					return err
				}
				accelOff[i] = NewOffuserFromMean(oldA, merged.AccelBias[i])
				if err := writeCalibbias(reg, CabinIMUAccel, "accel_"+axis, accelOff[i]); err != nil {
					return err
				}
			}
			merged.AccelOffuser = accelOff
			merged.AccelOffuserApplied = true
		}
		merged.OffuserApplied = merged.AccelOffuserApplied || merged.GyroOffuserApplied

		// Copy programmed values back onto caller's fit for the artifact.
		*fit = *merged

		cp := *cur
		cp.Devices = copyDeviceMap(cur.Devices)
		cp.Calibration = cur.Calibration
		cp.Calibration.GyroTCO = tco
		cp.Calibration.CabinIMU = merged
		if doGyro {
			mergeCalibbiasAttrs(&cp, CabinIMUGyro, "anglvel", gyroOff)
		}
		if doAccel {
			mergeCalibbiasAttrs(&cp, CabinIMUAccel, "accel", accelOff)
		}
		// Do not signal reload here: paused buffer loops would race
		// applyConfiguredAttrs against these calibbias writes and corrupt
		// OFFUSER over I²C. Resume refreshes from holder and applies once.
		holder.SetNoNotify(&cp)
		return config.Save(holder.Path(), &cp)
	})
}

// mergeIMUCal overlays accel and/or gyro fields from src onto a copy of prev.
func mergeIMUCal(prev, src *config.IMUCalResult, doAccel, doGyro bool) *config.IMUCalResult {
	var out config.IMUCalResult
	if prev != nil {
		out = *prev
	}
	if src == nil {
		return &out
	}
	if doAccel {
		out.AccelScale = src.AccelScale
		out.AccelBias = src.AccelBias
		out.ResidualRMS = src.ResidualRMS
		out.MeanNormMS2 = src.MeanNormMS2
		out.AccelFittedUTC = src.FittedUTC
		if src.FittedUTC != "" {
			out.FittedUTC = src.FittedUTC
		}
		// Accel-only warnings replace previous accel-ish warnings; keep simple.
		if len(src.Warnings) > 0 {
			out.Warnings = append([]string{}, src.Warnings...)
		}
	}
	if doGyro {
		out.GyroBias = src.GyroBias
		out.TempCalC = src.TempCalC
		out.GyroFaceRMS = src.GyroFaceRMS
		out.GyroFittedUTC = src.FittedUTC
		if src.FittedUTC != "" {
			out.FittedUTC = src.FittedUTC
		}
	}
	return &out
}

func readCalibbias(reg *Registry, device, channel string) (float64, error) {
	s, err := reg.ChannelAttr(device, channel, "calibbias")
	if err != nil {
		return 0, fmt.Errorf("sensors: read %s/%s calibbias: %w", device, channel, err)
	}
	v, err := strconv.ParseFloat(strings.TrimSpace(s), 64)
	if err != nil {
		return 0, fmt.Errorf("sensors: parse %s/%s calibbias %q: %w", device, channel, s, err)
	}
	return v, nil
}

func writeCalibbias(reg *Registry, device, channel string, v float64) error {
	val := strconv.FormatFloat(v, 'f', 9, 64)
	if err := reg.WriteAttr(device, channel, "calibbias", val); err != nil {
		return fmt.Errorf("sensors: write %s/%s calibbias=%s: %w", device, channel, val, err)
	}
	return nil
}

func mergeCalibbiasAttrs(cfg *config.Config, device, prefix string, off [3]float64) {
	if cfg.Devices == nil {
		cfg.Devices = map[string]config.Device{}
	}
	d := cfg.Devices[device]
	if d.Attrs == nil {
		d.Attrs = map[string]string{}
	} else {
		attrs := make(map[string]string, len(d.Attrs)+3)
		for k, v := range d.Attrs {
			attrs[k] = v
		}
		d.Attrs = attrs
	}
	for i, axis := range []string{"x", "y", "z"} {
		key := JoinIIOAttr(prefix+"_"+axis, "calibbias")
		d.Attrs[key] = strconv.FormatFloat(off[i], 'f', 9, 64)
	}
	cfg.Devices[device] = d
}

func copyDeviceMap(in map[string]config.Device) map[string]config.Device {
	out := make(map[string]config.Device, len(in))
	for k, v := range in {
		out[k] = copyDevice(v)
	}
	return out
}

func copyDevice(d config.Device) config.Device {
	out := d
	if len(d.Attrs) > 0 {
		out.Attrs = make(map[string]string, len(d.Attrs))
		for k, v := range d.Attrs {
			out.Attrs[k] = v
		}
	}
	if len(d.Channels) > 0 {
		out.Channels = make(map[string]config.Channel, len(d.Channels))
		for k, v := range d.Channels {
			out.Channels[k] = v
		}
	}
	return out
}
