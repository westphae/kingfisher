package pod

import (
	"strings"

	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
)

// parsePodAttrKey maps config/UI keys to a chip device name and attr.
// Accepts canonical "in_bmp581_sampling_frequency", legacy "in_mag_sampling_frequency",
// and device-level "sampling_frequency".
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
	if a != "sampling_frequency" {
		return "", "", false
	}
	if sid, ok := legacySettingsChannel[ch]; ok {
		return DefaultDeviceName(sid), a, true
	}
	for _, sid := range []wire.SensorID{wire.SensorStatic, wire.SensorMag, wire.SensorAirspeed} {
		if ch == DefaultDeviceName(sid) {
			return ch, a, true
		}
	}
	return "", "", false
}

// canonicalPodAttrKey is the form written to pod.attrs on save.
func canonicalPodAttrKey(device, attr string) string {
	return sensors.JoinIIOAttr(device, attr)
}
