package pod

import "github.com/westphae/kingfisher/internal/pod/wire"

// Default chip names when Hello has not reported device_name yet (proto v1 pods).
func DefaultDeviceName(sid wire.SensorID) string {
	switch sid {
	case wire.SensorStatic:
		return "bmp581"
	case wire.SensorMag:
		return "mmc5983"
	case wire.SensorAirspeed:
		return "ms4525"
	default:
		return ""
	}
}

// DefaultPodDeviceNames lists wing sensors for registry bootstrap before Hello.
func DefaultPodDeviceNames() []string {
	return []string{
		DefaultDeviceName(wire.SensorStatic),
		DefaultDeviceName(wire.SensorMag),
		DefaultDeviceName(wire.SensorAirspeed),
	}
}
