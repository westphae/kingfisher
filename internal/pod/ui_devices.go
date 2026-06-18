package pod

import (
	"github.com/westphae/kingfisher/internal/location"
	"github.com/westphae/kingfisher/internal/sensors"
)

// IsTelemetryDevice reports whether name is a wing-pod sensor tab (location pod).
func IsTelemetryDevice(reg *sensors.Registry, name string) bool {
	if reg != nil && reg.Location(name) == location.Pod {
		return name != DeviceName
	}
	for _, n := range DefaultPodDeviceNames() {
		if name == n {
			return true
		}
	}
	return false
}

// HideLegacyTab is the aggregate "pod" hub device (sticky cache); omit from UI tabs.
func HideLegacyTab(name string) bool {
	return name == DeviceName
}
