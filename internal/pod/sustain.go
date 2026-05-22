package pod

import "github.com/westphae/kingfisher/internal/pod/wire"

const (
	baseHzPod         = 10
	maxTickWorkUs     = 100_000
	usPerStaticRead   = 30_000
	usPerMagRead      = 2_000
	usPerAirspeedRead = 5_000
)

func readsPerTick(hz uint16) uint64 {
	if hz == 0 {
		return 0
	}
	return (uint64(hz) + baseHzPod - 1) / baseHzPod
}

func readsPerTickCapped(hz uint16) uint64 {
	r := readsPerTick(hz)
	if r > 3 {
		return 3
	}
	return r
}

// SustainableRates reports whether the combined schedule fits the pod bus
// budget (matches firmware rates::sustainable). BMP reads use the full
// requested cadence; faster sensors are planner-capped per tick.
func SustainableRates(staticHz, magHz, airHz uint16) bool {
	work := readsPerTick(staticHz)*usPerStaticRead +
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
