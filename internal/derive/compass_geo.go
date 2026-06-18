package derive

import (
	"fmt"
	"math"
	"strings"

	"github.com/westphae/magkal/pkg/field"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
)

const (
	compassAlignAccel = "accel"
	compassAlignWMM   = "wmm"
)

// effectiveAlignMethod returns "wmm" or "accel" from config or heuristics.
func effectiveAlignMethod(c *config.Compass, snap live.Snapshot, magDevice string) string {
	if c == nil {
		return compassAlignAccel
	}
	switch c.AlignMethod {
	case compassAlignWMM, compassAlignAccel:
		return c.AlignMethod
	}
	if magDevice != "" && !deviceHasAccel(snap, magDevice) {
		return compassAlignWMM
	}
	return compassAlignAccel
}

func deviceHasAccel(snap live.Snapshot, name string) bool {
	if name == "" {
		return false
	}
	sm, ok := snap.Devices[name]
	if !ok {
		return false
	}
	_, ok = extractAccel(sm.Values)
	return ok
}

func nedFieldUt(snap live.Snapshot) (field.Vec3, bool) {
	geo, ok := snap.Devices["geo"]
	if !ok {
		return field.Vec3{}, false
	}
	x, hx := geo.Values["field_x_nt"]
	y, hy := geo.Values["field_y_nt"]
	z, hz := geo.Values["field_z_nt"]
	if !hx || !hy || !hz {
		return field.Vec3{}, false
	}
	return field.Vec3{x / 1000, y / 1000, z / 1000}, true
}

func applySensorMount(cfg *config.Compass, sensor string, v field.Vec3) field.Vec3 {
	if cfg == nil {
		return v
	}
	if R, ok := cfg.SensorMountR[sensor]; ok {
		m := field.Mat3(R)
		if field.IsValidRot(m) {
			return field.ApplyRot(m, v)
		}
	}
	// Provisional mmc5983 default: left-hand fix via Z inversion.
	if strings.HasPrefix(strings.ToLower(sensor), "mmc5983") {
		return field.Vec3{X: v.X, Y: v.Y, Z: -v.Z}
	}
	return v
}

func fieldDot(a, b field.Vec3) float64 {
	return a.X*b.X + a.Y*b.Y + a.Z*b.Z
}

func rotZ(a float64) field.Mat3 {
	c, s := math.Cos(a), math.Sin(a)
	return field.Mat3{{c, -s, 0}, {s, c, 0}, {0, 0, 1}}
}

func publishNEDField(vals map[string]float64, bNedUt field.Vec3) {
	x, y, z, h, f, incl := field.FieldNED(bNedUt)
	vals["field_x_nt"] = x
	vals["field_y_nt"] = y
	vals["field_z_nt"] = z
	vals["field_h_nt"] = h
	vals["field_f_nt"] = f
	vals["inclination"] = incl
}

func headingTrueFromAircraftToEarth(R field.Mat3) float64 {
	// Nose axis is +X in aircraft FRD. Map to earth and extract azimuth from north/east.
	nose := field.Vec3{X: 1, Y: 0, Z: 0}
	earth := field.ApplyRot(R, nose)
	return field.HeadingDeg360(math.Atan2(earth.Y, earth.X) * 180 / math.Pi)
}

func solveAircraftToEarthMagDown(magAircraft, bEarth field.Vec3) (field.Mat3, error) {
	// TODO(ahrs-frd-ned): Harmonize AHRS package conventions to FRD/NED and share
	// a single aircraft->earth rotation pipeline across compass and AHRS pages.
	down := field.Vec3{X: 0, Y: 0, Z: 1}
	hA, ok := projectHorizDown(magAircraft, down)
	if !ok {
		return field.Mat3{}, fmt.Errorf("mag field is vertical in aircraft frame")
	}
	hE, ok := projectHorizDown(bEarth, down)
	if !ok {
		return field.Mat3{}, fmt.Errorf("geomag field is vertical in earth frame")
	}
	delta := math.Atan2(hA.X*hE.Y-hA.Y*hE.X, hA.X*hE.X+hA.Y*hE.Y)
	return rotZ(delta), nil
}

func projectHorizDown(v, down field.Vec3) (field.Vec3, bool) {
	dn := math.Sqrt(fieldDot(down, down))
	if dn == 0 {
		return field.Vec3{}, false
	}
	d := field.Vec3{down.X / dn, down.Y / dn, down.Z / dn}
	c := fieldDot(v, d)
	h := field.Vec3{v.X - c*d.X, v.Y - c*d.Y, v.Z - c*d.Z}
	n := math.Sqrt(fieldDot(h, h))
	if n == 0 {
		return field.Vec3{}, false
	}
	return field.Vec3{h.X / n, h.Y / n, h.Z / n}, true
}

func alignMethodName(m string) error {
	if m == "" || m == compassAlignAccel || m == compassAlignWMM {
		return nil
	}
	return fmt.Errorf("compass align_method must be %q or %q", compassAlignAccel, compassAlignWMM)
}
