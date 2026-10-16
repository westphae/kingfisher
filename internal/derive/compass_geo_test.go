package derive

import (
	"math"
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
	magAircraft := field.Vec3{X: 10, Y: 0, Z: 30}
	bEarth := field.Vec3{X: 0, Y: 10, Z: 30}
	R, err := solveAircraftToEarthMagDown(magAircraft, bEarth)
	if err != nil {
		t.Fatalf("solve: %v", err)
	}
	got := field.ApplyRot(R, magAircraft)
	ga, ok := projectHorizDown(got, field.Vec3{X: 0, Y: 0, Z: 1})
	if !ok {
		t.Fatal("got horiz failed")
	}
	ge, ok := projectHorizDown(bEarth, field.Vec3{X: 0, Y: 0, Z: 1})
	if !ok {
		t.Fatal("earth horiz failed")
	}
	if (ga.X-ge.X)*(ga.X-ge.X)+(ga.Y-ge.Y)*(ga.Y-ge.Y) > 1e-6 {
		t.Fatalf("horizontal mismatch got=%+v want=%+v", ga, ge)
	}
}

func TestWMMLevelHeadingTracksYaw(t *testing.T) {
	// Declination 0: earth horizontal field along +X (magnetic north).
	// Body mag along the nose (+X) is a north heading; mag along -Y is east
	// (field, fixed in earth, has swung to the left wing).
	earth := field.Vec3{X: 20, Y: 0, Z: 40}
	north, _, ok := wmmLevelHeading(field.Vec3{X: 20, Y: 0, Z: 40}, earth, 0)
	if !ok {
		t.Fatal("north solve failed")
	}
	if north != 360 && north != 0 {
		t.Fatalf("nose-aligned mag heading=%v want 360", north)
	}
	east, _, ok := wmmLevelHeading(field.Vec3{X: 0, Y: -20, Z: 40}, earth, 0)
	if !ok {
		t.Fatal("east solve failed")
	}
	if math.Abs(east-90) > 0.05 {
		t.Fatalf("left-wing mag heading=%v want 90", east)
	}
	diff := math.Abs(north - east)
	if diff > 180 {
		diff = 360 - diff
	}
	if diff < 80 {
		t.Fatalf("heading did not track the turn: north=%v east=%v", north, east)
	}
}

func TestApplySensorMountMMCDefault(t *testing.T) {
	cfg := &config.Compass{SensorMountR: map[string][3][3]float64{}}
	v := field.Vec3{X: 1, Y: 2, Z: 3}
	got := applySensorMount(cfg, "mmc5983", v)
	if got.Z != -3 {
		t.Fatalf("mmc default should invert z, got %+v", got)
	}
}
