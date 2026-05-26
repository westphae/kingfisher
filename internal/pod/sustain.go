package pod

import "github.com/westphae/kingfisher/internal/pod/wire"

const (
	baseHzPod         = 10
	maxTickWorkUs     = 100_000
	usPerStaticDrain  = 3_000
	usPerStaticFrame  = 400
	usPerMagRead      = 1_200
	usPerAirspeedRead = 5_000
	maxReadsPerTick   = 3
)

func readsPerTick(hz uint16) uint64 {
	if hz == 0 {
		return 0
	}
	return (uint64(hz) + baseHzPod - 1) / baseHzPod
}

func readsPerTickCapped(hz uint16) uint64 {
	r := readsPerTick(hz)
	if r > maxReadsPerTick {
		return maxReadsPerTick
	}
	return r
}

// staticTickWorkUs estimates BMP581 FIFO drain cost per poll tick
// (matches firmware/pod/src/rates.rs static_tick_work_us).
func staticTickWorkUs(hz uint16) uint64 {
	if hz == 0 {
		return 0
	}
	frames := readsPerTick(hz)
	if frames < 1 {
		frames = 1
	}
	return uint64(usPerStaticDrain) + frames*uint64(usPerStaticFrame)
}

// SustainableRates reports whether the combined schedule fits the pod bus
// budget (matches firmware rates::sustainable).
func SustainableRates(staticHz, magHz, airHz uint16) bool {
	work := staticTickWorkUs(staticHz) +
		readsPerTickCapped(magHz)*usPerMagRead +
		readsPerTickCapped(airHz)*usPerAirspeedRead
	return work <= maxTickWorkUs
}

// RatesAfterChange returns the three sensor Hz values after applying one change.
func RatesAfterChange(cur map[wire.SensorID]uint16, sid wire.SensorID, newHz uint16) (staticHz, magHz, airHz uint16) {
	staticHz = cur[wire.SensorStatic]
	magHz = cur[wire.SensorMag]
	airHz = cur[wire.SensorAirspeed]
	switch sid {
	case wire.SensorStatic:
		staticHz = newHz
	case wire.SensorMag:
		magHz = newHz
	case wire.SensorAirspeed:
		airHz = newHz
	}
	return
}
