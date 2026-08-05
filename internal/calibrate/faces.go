package calibrate

// Face identifies one of the six body-frame face-down orientations.
type Face string

const (
	FacePlusX  Face = "+X"
	FaceMinusX Face = "-X"
	FacePlusY  Face = "+Y"
	FaceMinusY Face = "-Y"
	FacePlusZ  Face = "+Z"
	FaceMinusZ Face = "-Z"
	// FaceStill is the single unspecified orientation for cabin gyro cal.
	FaceStill Face = "still"
)

// Faces is the guided tumble order.
var Faces = []Face{
	FacePlusZ, FaceMinusZ,
	FacePlusX, FaceMinusX,
	FacePlusY, FaceMinusY,
}

// AxisIndex returns 0/1/2 for X/Y/Z.
func (f Face) AxisIndex() int {
	switch f {
	case FacePlusX, FaceMinusX:
		return 0
	case FacePlusY, FaceMinusY:
		return 1
	default:
		return 2
	}
}

// Sign is +1 for +axis faces, -1 for −axis.
func (f Face) Sign() float64 {
	switch f {
	case FacePlusX, FacePlusY, FacePlusZ:
		return 1
	default:
		return -1
	}
}

// Label is short operator copy for the cube diagram.
func (f Face) Label() string {
	switch f {
	case FacePlusZ:
		return "Rest on the bottom (−Z face down; +Z up)"
	case FaceMinusZ:
		return "Rest upside-down (+Z face down; −Z up)"
	case FacePlusX:
		return "Rest on the −X side (+X axis up)"
	case FaceMinusX:
		return "Rest on the +X side (−X axis up)"
	case FacePlusY:
		return "Rest on the −Y side (+Y axis up)"
	case FaceMinusY:
		return "Rest on the +Y side (−Y axis up)"
	case FaceStill:
		return "Any stable orientation on the table"
	default:
		return string(f)
	}
}

func (f Face) Valid() bool {
	if f == FaceStill {
		return true
	}
	for _, x := range Faces {
		if x == f {
			return true
		}
	}
	return false
}
