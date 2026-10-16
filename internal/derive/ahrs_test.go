package derive

import (
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
)

// A cabin IMU (accel/gyro, no mag) plus a pod MMC5983 must fuse the pod mag
// brought into the fuselage frame via the compass mount correction — NOT the
// raw pod-axis vector. With no SensorMountR configured, applySensorMount's
// default mmc5983 fix inverts Z, so M3 must be the negated pod Z.
func TestBuildMeasurementPodMagMountCorrected(t *testing.T) {
	now := time.Now().UnixNano()
	snap := live.Snapshot{
		ServerTsNs: now,
		Devices: map[string]live.Sample{
			"icm45686": {TsNs: now, Values: map[string]float64{
				"accel_x": 0, "accel_y": 0, "accel_z": 9.80665,
				"anglvel_x": 0, "anglvel_y": 0, "anglvel_z": 0,
			}},
			"mmc5983": {TsNs: now, Values: map[string]float64{
				"mag_x_ut": 20, "mag_y_ut": 5, "mag_z_ut": 40,
			}},
		},
	}
	m, magSrc := buildMeasurement(snap, gps.Fix{}, &config.Compass{})
	if m == nil {
		t.Fatal("nil measurement")
	}
	if magSrc != "mmc5983" {
		t.Fatalf("magSrc=%q want mmc5983", magSrc)
	}
	if !m.MValid {
		t.Fatal("MValid should be true for a fresh pod mag")
	}
	if m.M1 != 20 || m.M2 != 5 || m.M3 != -40 {
		t.Fatalf("pod mag not mount-corrected: M=(%v,%v,%v) want (20,5,-40)", m.M1, m.M2, m.M3)
	}
}

// A stale pod mag (last sample older than the TTL) must not be fused — the
// fused heading must fall back to attitude-only rather than locking to a
// frozen magnetic vector.
func TestBuildMeasurementRejectsStalePodMag(t *testing.T) {
	now := time.Now().UnixNano()
	snap := live.Snapshot{
		ServerTsNs: now,
		Devices: map[string]live.Sample{
			"icm45686": {TsNs: now, Values: map[string]float64{
				"accel_x": 0, "accel_y": 0, "accel_z": 9.80665,
				"anglvel_x": 0, "anglvel_y": 0, "anglvel_z": 0,
			}},
			"mmc5983": {TsNs: now - 5*int64(time.Second), Values: map[string]float64{
				"mag_x_ut": 20, "mag_y_ut": 5, "mag_z_ut": 40,
			}},
		},
	}
	m, magSrc := buildMeasurement(snap, gps.Fix{}, &config.Compass{})
	if m == nil {
		t.Fatal("nil measurement")
	}
	if m.MValid {
		t.Fatal("MValid should be false for a stale pod mag")
	}
	if magSrc != "mmc5983 (stale)" {
		t.Fatalf("magSrc=%q want \"mmc5983 (stale)\"", magSrc)
	}
}

// The cabin ICM-45686 is two IIO devices. Both must land in one measurement;
// picking either device alone zeros gyro or accel, and map iteration would
// flap between them.
func TestFindIMUMergesSplitAccelGyro(t *testing.T) {
	snap := live.Snapshot{
		Devices: map[string]live.Sample{
			"icm45686-accel": {TsNs: 10, Values: map[string]float64{
				"accel_x": 0, "accel_y": 0, "accel_z": 9.80665,
			}},
			"icm45686-gyro": {TsNs: 11, Values: map[string]float64{
				"anglvel_x": 0.01, "anglvel_y": -0.02, "anglvel_z": 0.03,
			}},
			"bmp581": {Values: map[string]float64{"static_pressure_pa": 101325}},
		},
	}
	var name string
	for i := 0; i < 30; i++ {
		n, sm, ok := findIMU(snap)
		if !ok {
			t.Fatal("expected merged IMU")
		}
		if i == 0 {
			name = n
		} else if n != name {
			t.Fatalf("IMU source flapped %q -> %q", name, n)
		}
		if sm.Values["accel_z"] != 9.80665 || sm.Values["anglvel_z"] != 0.03 {
			t.Fatalf("merged values = %+v", sm.Values)
		}
	}
	m, _ := buildMeasurement(snap, gps.Fix{}, nil)
	if m == nil {
		t.Fatal("nil measurement")
	}
	if m.A3 < 0.9 || m.A3 > 1.1 {
		t.Fatalf("A3=%v want ~1 g from the accel device", m.A3)
	}
	if m.B3 == 0 {
		t.Fatal("B3=0; gyro device was dropped")
	}
}

// A single-chip IMU+mag (icm magn_*) is used raw and is unaffected by the
// pod mount correction.
func TestBuildMeasurementSingleChipMagRaw(t *testing.T) {
	now := time.Now().UnixNano()
	snap := live.Snapshot{
		ServerTsNs: now,
		Devices: map[string]live.Sample{
			"icm20948": {TsNs: now, Values: map[string]float64{
				"accel_x": 0, "accel_y": 0, "accel_z": 9.80665,
				"anglvel_x": 0, "anglvel_y": 0, "anglvel_z": 0,
				"magn_x": 11, "magn_y": 22, "magn_z": 33,
			}},
		},
	}
	m, magSrc := buildMeasurement(snap, gps.Fix{}, &config.Compass{})
	if m == nil || !m.MValid {
		t.Fatal("expected valid measurement with mag")
	}
	if magSrc != "icm20948" {
		t.Fatalf("magSrc=%q want icm20948", magSrc)
	}
	if m.M1 != 11 || m.M2 != 22 || m.M3 != 33 {
		t.Fatalf("single-chip mag should be raw: M=(%v,%v,%v)", m.M1, m.M2, m.M3)
	}
}
