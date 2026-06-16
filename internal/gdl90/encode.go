package gdl90

import "math"

const (
	lonLatResolution = 180.0 / 8388608.0
	trackResolution  = 360.0 / 256.0
	mpsPerKt         = 0.514444444
	msToFpmFactor = 196.850394
)

const (
	invalidAngle = int16(0x7FFF)
	invalidU16   = uint16(0xFFFF)
)

// EncodeLatLng packs latitude or longitude for GDL90 ownship reports.
func EncodeLatLng(deg float64) [3]byte {
	v := deg / lonLatResolution
	wk := int32(v)
	return [3]byte{
		byte((wk & 0xFF0000) >> 16),
		byte((wk & 0x00FF00) >> 8),
		byte(wk & 0x0000FF),
	}
}

func mpsToKt(mps float64) uint16 {
	if math.IsNaN(mps) || mps < 0 {
		return 0
	}
	return uint16(mps/mpsPerKt + 0.5)
}

func msToFpm(vsMs float64) int16 {
	if math.IsNaN(vsMs) {
		return int16(0x800)
	}
	return int16(vsMs*msToFpmFactor + 0.5)
}

func roundToInt16(v float64) int16 {
	if math.IsNaN(v) {
		return invalidAngle
	}
	if v >= 0 {
		return int16(v + 0.5)
	}
	return int16(v - 0.5)
}

func encodeTrackDeg(track float64) uint8 {
	if math.IsNaN(track) {
		return 0
	}
	t := float32(track) + trackResolution/2
	for t > 360 {
		t -= 360
	}
	for t < 0 {
		t += 360
	}
	return uint8(t / trackResolution)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func altFtToOwnship12(altFt float64) uint16 {
	if math.IsNaN(altFt) {
		return 0xFFF
	}
	altf := (altFt + 1000) / 25
	return uint16(altf) & 0xFFF
}
