package gdl90

import (
	"encoding/hex"
	"math"
	"time"
)

// Situation holds the latest values used to build outbound GDL90 messages.
type Situation struct {
	GPSValid    bool
	AHRSValid   bool
	BaroValid   bool
	GPSNACp     uint8 // 0–15; default 9 when unknown

	Lat, Lon       float64
	AltMSLM        float64 // meters
	GroundSpeedMps float64
	TrackDeg       float64
	ClimbMs        float64

	PressureAltFt float64
	BaroVSFpm     float64

	RollDeg, PitchDeg, HeadingDeg float64
	SlipSkidDeg, TurnRateDegS     float64
	GLoad                         float64
	IASKt, TASKt                  float64

	Callsign      string // up to 8 chars in ownship
	OwnshipModeS  string // hex ICAO, e.g. F00000
	DeviceShort   string
	DeviceLong    string
}

// Heartbeat builds GDL90 message 0x00.
func Heartbeat(gpsValid bool) []byte {
	msg := make([]byte, 7)
	msg[0] = 0x00
	msg[1] = 0x01 | 0x10 // UAT initialized + address talkback
	if gpsValid {
		msg[1] |= 0x80
	}
	nowUTC := time.Now().UTC()
	midnight := time.Date(nowUTC.Year(), nowUTC.Month(), nowUTC.Day(), 0, 0, 0, 0, time.UTC)
	sec := uint32(nowUTC.Sub(midnight).Seconds())
	msg[2] = byte(((sec >> 16) << 7) | 0x1) // UTC OK
	msg[3] = byte(sec & 0xFF)
	msg[4] = byte((sec & 0xFFFF) >> 8)
	return Pack(msg)
}

// StratuxHeartbeat builds non-standard message 0xCC.
func StratuxHeartbeat(gpsValid, ahrsValid bool) []byte {
	msg := make([]byte, 2)
	msg[0] = 0xCC
	if gpsValid {
		msg[1] = 0x02
	}
	if ahrsValid {
		msg[1] |= 0x01
	}
	msg[1] |= byte(1 << 2) // protocol version 1
	return Pack(msg)
}

// ForeFlightID builds ForeFlight device ID message 0x65 sub 0x00.
func ForeFlightID(shortName, longName string) []byte {
	msg := make([]byte, 39)
	msg[0] = 0x65
	msg[1] = 0x00
	msg[2] = 0x01
	for i := 3; i <= 10; i++ {
		msg[i] = 0xFF
	}
	copy(msg[11:], truncate(shortName, 8))
	copy(msg[19:], truncate(longName, 16))
	msg[38] = 0x01 // MSL altitude for ownship geo report
	return Pack(msg)
}

// Ownship builds message 0x0A. Returns nil when GPS is invalid.
func Ownship(s Situation) []byte {
	if !s.GPSValid {
		return nil
	}
	msg := make([]byte, 28)
	msg[0] = 0x0A

	code, _ := hex.DecodeString(s.OwnshipModeS)
	if len(code) == 3 && code[0] != 0xF0 && code[0] != 0x00 {
		msg[1] = 0x00
		msg[2], msg[3], msg[4] = code[0], code[1], code[2]
	} else {
		msg[1] = 0x01
		msg[2], msg[3], msg[4] = 0xF0, 0x00, 0x00
	}

	lat := EncodeLatLng(s.Lat)
	msg[5], msg[6], msg[7] = lat[0], lat[1], lat[2]
	lng := EncodeLatLng(s.Lon)
	msg[8], msg[9], msg[10] = lng[0], lng[1], lng[2]

	alt := uint16(0xFFF)
	if s.BaroValid {
		alt = altFtToOwnship12(s.PressureAltFt)
	}
	msg[11] = byte((alt & 0xFF0) >> 4)
	msg[12] = byte((alt & 0x00F) << 4)
	if s.GPSValid {
		msg[12] |= 0x09 // airborne + true track
	}

	nacp := s.GPSNACp
	if nacp == 0 {
		nacp = 9
	}
	msg[13] = byte(0x80 | (nacp & 0x0F))

	gdSpeed := mpsToKt(s.GroundSpeedMps)
	msg[14] = byte((gdSpeed & 0xFF0) >> 4)
	msg[15] = byte((gdSpeed & 0x00F) << 4)

	vv := int16(0x800)
	if !math.IsNaN(s.ClimbMs) {
		vv = msToFpm(s.ClimbMs)
	}
	msg[15] |= byte((vv & 0x0F00) >> 8)
	msg[16] = byte(vv & 0xFF)

	msg[17] = encodeTrackDeg(s.TrackDeg)
	msg[18] = 0x01 // light aircraft

	reg := truncate(s.Callsign, 8)
	if reg == "" {
		reg = "Kingfshr"
	}
	for i := 0; i < len(reg); i++ {
		msg[19+i] = reg[i]
	}
	return Pack(msg)
}

// OwnshipGeoAlt builds message 0x0B. Returns nil when GPS is invalid.
func OwnshipGeoAlt(s Situation) []byte {
	if !s.GPSValid {
		return nil
	}
	msg := make([]byte, 5)
	msg[0] = 0x0B
	altFt := s.AltMSLM / 0.3048
	alt := int16(altFt / 5)
	msg[1] = byte(alt >> 8)
	msg[2] = byte(alt & 0xFF)
	msg[3] = 0x00
	msg[4] = 0x0A
	return Pack(msg)
}

// AHRSReport builds Stratux extended AHRS message 0x4C 0x45.
func AHRSReport(s Situation) []byte {
	msg := make([]byte, 24)
	msg[0] = 0x4C
	msg[1] = 0x45
	msg[2] = 0x01
	msg[3] = 0x01

	pitch := invalidAngle
	roll := invalidAngle
	hdg := invalidAngle
	slip := invalidAngle
	yawRate := invalidAngle
	g := invalidAngle
	airspeed := invalidAngle
	palt := invalidU16
	vs := invalidAngle

	if s.AHRSValid {
		if !math.IsNaN(s.PitchDeg) {
			pitch = roundToInt16(s.PitchDeg * 10)
		}
		if !math.IsNaN(s.RollDeg) {
			roll = roundToInt16(s.RollDeg * 10)
		}
		if !math.IsNaN(s.HeadingDeg) {
			hdg = roundToInt16(s.HeadingDeg * 10)
		}
		if !math.IsNaN(s.SlipSkidDeg) {
			slip = roundToInt16(-s.SlipSkidDeg * 10)
		}
		if !math.IsNaN(s.TurnRateDegS) {
			yawRate = roundToInt16(s.TurnRateDegS * 10)
		}
		if !math.IsNaN(s.GLoad) {
			g = roundToInt16(s.GLoad * 10)
		}
	}
	if !math.IsNaN(s.IASKt) && s.IASKt > 0 {
		airspeed = roundToInt16(s.IASKt * 10)
	}
	if s.BaroValid {
		palt = uint16(s.PressureAltFt + 5000.5)
		if !math.IsNaN(s.BaroVSFpm) {
			vs = roundToInt16(s.BaroVSFpm)
		}
	}

	putI16 := func(off int, v int16) {
		msg[off] = byte((v >> 8) & 0xFF)
		msg[off+1] = byte(v & 0xFF)
	}
	putI16(4, roll)
	putI16(6, pitch)
	putI16(8, hdg)
	putI16(10, slip)
	putI16(12, yawRate)
	putI16(14, g)
	putI16(16, airspeed)
	msg[18] = byte((palt >> 8) & 0xFF)
	msg[19] = byte(palt & 0xFF)
	putI16(20, vs)
	msg[22] = 0x7F
	msg[23] = 0xFF
	return Pack(msg)
}

// ForeFlightAHRS builds ForeFlight AHRS message 0x65 sub 0x01.
func ForeFlightAHRS(s Situation) []byte {
	msg := make([]byte, 12)
	msg[0] = 0x65
	msg[1] = 0x01

	pitch := invalidAngle
	roll := invalidAngle
	hdg := invalidU16
	ias := invalidU16
	tas := invalidU16

	if s.AHRSValid {
		if !math.IsNaN(s.PitchDeg) {
			pitch = roundToInt16(s.PitchDeg * 10)
		}
		if !math.IsNaN(s.RollDeg) {
			roll = roundToInt16(s.RollDeg * 10)
		}
		if !math.IsNaN(s.HeadingDeg) {
			h := int(s.HeadingDeg*10+0.5) % 3600
			if h < 0 {
				h += 3600
			}
			hdg = uint16(h)
		}
	}
	if !math.IsNaN(s.IASKt) && s.IASKt > 0 {
		ias = uint16(s.IASKt + 0.5)
	}
	if !math.IsNaN(s.TASKt) && s.TASKt > 0 {
		tas = uint16(s.TASKt + 0.5)
	}

	msg[2] = byte((roll >> 8) & 0xFF)
	msg[3] = byte(roll & 0xFF)
	msg[4] = byte((pitch >> 8) & 0xFF)
	msg[5] = byte(pitch & 0xFF)
	msg[6] = byte((hdg >> 8) & 0xFF)
	msg[7] = byte(hdg & 0xFF)
	msg[8] = byte((ias >> 8) & 0xFF)
	msg[9] = byte(ias & 0xFF)
	msg[10] = byte((tas >> 8) & 0xFF)
	msg[11] = byte(tas & 0xFF)
	return Pack(msg)
}
