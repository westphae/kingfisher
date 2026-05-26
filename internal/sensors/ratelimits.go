package sensors

import (
	"math"
	"strconv"
	"strings"
)

// bmp280ConvMs is typical active conversion time per channel (ms) from the
// BMP280 datasheet rev 1.20, Table 6. Buffered capture runs temp then pressure
// per trigger, so the minimum period is their sum.
var bmp280ConvMs = map[int]struct{ press, temp float64}{
	1:  {2.3, 1.8},
	2:  {5.5, 2.2},
	4:  {10.5, 3.8},
	8:  {20.5, 7.5},
	16: {43.2, 12.5},
}

const bufferedRateMargin = 1.05 // headroom for hrtimer jitter and filter settle

// icm20948MagAuxMaxHz is the AK09916 aux-master cadence wired in icm20948-mod
// at probe (SLV4_CTRL=10 against the default ~1.1 kHz gyro rate → ~100 Hz).
// Faster hrtimer pacing can wedge the mag I²C path (driver comment at probe).
const icm20948MagAuxMaxHz = 100.0

// MaxBufferedHz returns the highest sustainable buffered sample rate (Hz) for
// this reader's chip and current oversampling attrs, or false if unknown.
func MaxBufferedHz(r Reader) (float64, bool) {
	switch strings.ToLower(r.Name()) {
	case "bmp280":
		return bmp280MaxBufferedHz(r), true
	case "icm20948":
		return icm20948MaxBufferedHz(r), true
	default:
		return 0, false
	}
}

func bmp280MaxBufferedHz(r Reader) float64 {
	pOSR := oversamplingRatio(r, "pressure", 1)
	tOSR := oversamplingRatio(r, "temp", 1)
	tP, okP := bmp280ConvMs[pOSR]
	if !okP {
		pOSR = 1
		tP = bmp280ConvMs[1]
	}
	tT, okT := bmp280ConvMs[tOSR]
	if !okT {
		tOSR = 1
		tT = bmp280ConvMs[1]
	}
	ms := tP.press + tT.temp
	if ms <= 0 {
		return 0
	}
	hz := 1000.0 / (ms * bufferedRateMargin)
	return floorBufferedHz(hz)
}

func icm20948MaxBufferedHz(r Reader) float64 {
	_ = r // fixed mag-aux ceiling; DLPF only affects signal bandwidth, not wedge risk
	return floorBufferedHz(icm20948MagAuxMaxHz / bufferedRateMargin)
}

func floorBufferedHz(hz float64) float64 {
	return math.Floor(hz*2) / 2
}

func oversamplingRatio(r Reader, channel string, defaultOSR int) int {
	v, err := r.ChannelAttr(channel, "oversampling_ratio")
	if err != nil {
		return defaultOSR
	}
	n, err := strconv.Atoi(strings.TrimSpace(v))
	if err != nil || n <= 0 {
		return defaultOSR
	}
	return n
}

// clampBufferedHz returns a sample rate suitable for buffered capture, capped
// to MaxBufferedHz when the chip model is known.
func clampBufferedHz(r Reader, hz float64) float64 {
	if hz <= 0 {
		hz = 10
	}
	max, ok := MaxBufferedHz(r)
	if !ok || hz <= max {
		return hz
	}
	return max
}
