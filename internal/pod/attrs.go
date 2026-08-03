package pod

import (
	"strings"

	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
)

// BMP581 attr names (IIO-style, device-level on the bmp581 tab).
const (
	AttrOversamplingPressure = "oversampling_pressure"
	AttrOversamplingTemp     = "oversampling_temp"
	AttrIIRPressure          = "iir_pressure"
	AttrIIRTemp              = "iir_temp"
)

// parsePodAttrKey maps config/UI keys to a chip device name and attr.
// Accepts canonical "in_bmp581_sampling_frequency", legacy "in_mag_sampling_frequency",
// BMP581 OSR/IIR keys, and device-level "sampling_frequency".
func parsePodAttrKey(full string) (device, attr string, ok bool) {
	if full == "sampling_frequency" {
		return "", "sampling_frequency", true
	}
	if strings.HasPrefix(full, "in_") && strings.HasSuffix(full, "_sampling_frequency") {
		mid := strings.TrimPrefix(full, "in_")
		mid = strings.TrimSuffix(mid, "_sampling_frequency")
		prefix := mid
		if i := strings.IndexByte(mid, '_'); i > 0 {
			prefix = mid[:i]
		}
		if sid, sidOK := legacySettingsChannel[prefix]; sidOK {
			return DefaultDeviceName(sid), "sampling_frequency", true
		}
		if prefix == DefaultDeviceName(wire.SensorStatic) ||
			prefix == DefaultDeviceName(wire.SensorMag) ||
			prefix == DefaultDeviceName(wire.SensorAirspeed) {
			return prefix, "sampling_frequency", true
		}
	}

	ch, a := sensors.SplitIIOAttr(full)
	if a == "" {
		return "", "", false
	}
	dev := resolvePodDevice(ch)
	if dev == "" {
		return "", "", false
	}
	switch a {
	case "sampling_frequency",
		AttrOversamplingPressure, AttrOversamplingTemp,
		AttrIIRPressure, AttrIIRTemp:
		return dev, a, true
	default:
		return "", "", false
	}
}

func resolvePodDevice(chOrDev string) string {
	if sid, ok := legacySettingsChannel[chOrDev]; ok {
		return DefaultDeviceName(sid)
	}
	for _, sid := range []wire.SensorID{
		wire.SensorStatic, wire.SensorMag, wire.SensorAirspeed, wire.SensorBattery,
	} {
		if chOrDev == DefaultDeviceName(sid) {
			return chOrDev
		}
	}
	// in_static_oversampling_pressure → channel "static"
	if sid, ok := legacySettingsChannel[chOrDev]; ok {
		return DefaultDeviceName(sid)
	}
	return ""
}

func wireAttrKeyFor(attr string) (wire.AttrKey, bool) {
	switch attr {
	case AttrOversamplingPressure:
		return wire.AttrBmpOsrPress, true
	case AttrOversamplingTemp:
		return wire.AttrBmpOsrTemp, true
	case AttrIIRPressure:
		return wire.AttrBmpIirPress, true
	case AttrIIRTemp:
		return wire.AttrBmpIirTemp, true
	default:
		return 0, false
	}
}
