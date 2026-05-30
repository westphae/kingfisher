package derive

import (
	"testing"

	"github.com/westphae/magkal/pkg/field"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
)

func TestEffectiveAlignMethodPodMag(t *testing.T) {
	snap := live.Snapshot{
		Devices: map[string]live.Sample{
			"mmc5983": {Values: map[string]float64{"mag_x_ut": 1, "mag_y_ut": 0, "mag_z_ut": 0}},
			"icm20948": {
				Values: map[string]float64{
					"accel_x": 0, "accel_y": 0, "accel_z": 9.81,
				},
			},
		},
	}
	c := &config.Compass{Magnetometer: "mmc5983"}
	if got := effectiveAlignMethod(c, snap, "mmc5983"); got != compassAlignWMM {
		t.Fatalf("got %q want wmm", got)
	}
}

func TestSolveAircraftToEarthMagDown(t *testing.T) {
	magAircraft := field.Vec3{10, 0, 30}
	bEarth := field.Vec3{0, 10, 30}
	R, err := solveAircraftToEarthMagDown(magAircraft, bEarth)
	if err != nil {
		t.Fatalf("solve: %v", err)
	}
	got := field.ApplyRot(R, magAircraft)
	ga, ok := projectHorizDown(got, field.Vec3{0, 0, 1})
	if !ok {
		t.Fatal("got horiz failed")
	}
	ge, ok := projectHorizDown(bEarth, field.Vec3{0, 0, 1})
	if !ok {
		t.Fatal("earth horiz failed")
	}
	if (ga.X-ge.X)*(ga.X-ge.X)+(ga.Y-ge.Y)*(ga.Y-ge.Y) > 1e-6 {
		t.Fatalf("horizontal mismatch got=%+v want=%+v", ga, ge)
	}
}

func TestApplySensorMountMMCDefault(t *testing.T) {
	cfg := &config.Compass{SensorMountR: map[string][3][3]float64{}}
	v := field.Vec3{1, 2, 3}
	got := applySensorMount(cfg, "mmc5983", v)
	if got.Z != -3 {
		t.Fatalf("mmc default should invert z, got %+v", got)
	}
}
