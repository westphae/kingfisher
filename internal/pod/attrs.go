package pod

import (
	"strings"

	"github.com/westphae/kingfisher/internal/sensors"
)

// parsePodAttrKey maps config/UI keys to per-sensor settings channels.
// Accepts canonical "in_mag_sampling_frequency" and legacy per-data-channel
// keys such as "in_mag_x_sampling_frequency" from older UI versions.
func parsePodAttrKey(full string) (channel, attr string, ok bool) {
	ch, a := sensors.SplitIIOAttr(full)
	if a == "sampling_frequency" {
		if sid, ok := channelToSensor[ch]; ok && sensorSettingsChannel[sid] == ch {
			return ch, a, true
		}
	}
	if !strings.HasPrefix(full, "in_") || !strings.HasSuffix(full, "_sampling_frequency") {
		return "", "", false
	}
	mid := strings.TrimPrefix(full, "in_")
	mid = strings.TrimSuffix(mid, "_sampling_frequency")
	prefix := mid
	if i := strings.IndexByte(mid, '_'); i > 0 {
		prefix = mid[:i]
	}
	if sid, sidOK := channelToSensor[prefix]; sidOK {
		return sensorSettingsChannel[sid], "sampling_frequency", true
	}
	return "", "", false
}

// canonicalPodAttrKey is the form written to pod.attrs on save.
func canonicalPodAttrKey(channel, attr string) string {
	return sensors.JoinIIOAttr(channel, attr)
}
