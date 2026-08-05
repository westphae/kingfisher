package calibrate

import "math"

const (
	// Face detection: dominant accel axis must carry most of ‖a‖ and be ~g-scale.
	faceDominanceMin = 0.82 // |a_axis| / ‖a‖
	facePrimaryMin   = 6.5  // |a_axis| m/s² (~0.66 g)
)

// DetectFace returns the body face whose axis is closest to gravity
// (largest |component|, sign of that component). ok is false while tumbling
// or when no axis clearly dominates.
func DetectFace(a [3]float64) (Face, float64, bool) {
	n := math.Hypot(a[0], math.Hypot(a[1], a[2]))
	if n < facePrimaryMin {
		return "", 0, false
	}
	axis := 0
	for i := 1; i < 3; i++ {
		if math.Abs(a[i]) > math.Abs(a[axis]) {
			axis = i
		}
	}
	prim := a[axis]
	dom := math.Abs(prim) / n
	if dom < faceDominanceMin || math.Abs(prim) < facePrimaryMin {
		return "", dom, false
	}
	var f Face
	switch axis {
	case 0:
		if prim >= 0 {
			f = FacePlusX
		} else {
			f = FaceMinusX
		}
	case 1:
		if prim >= 0 {
			f = FacePlusY
		} else {
			f = FaceMinusY
		}
	default:
		if prim >= 0 {
			f = FacePlusZ
		} else {
			f = FaceMinusZ
		}
	}
	return f, dom, true
}
