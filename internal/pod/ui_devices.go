package pod

import (
	"github.com/westphae/kingfisher/internal/location"
	"github.com/westphae/kingfisher/internal/sensors"
)

// RegistryDevice maps a telemetry UI device to the pod reader registry name.
func RegistryDevice(uiDevice string) (registryName string, ok bool) {
	if uiDevice == DeviceName {
		return DeviceName, true
	}
	if _, ok := legacySettingsChannel[uiDevice]; ok {
		return DeviceName, true
	}
	for _, n := range DefaultPodDeviceNames() {
		if uiDevice == n {
			return DeviceName, true
		}
	}
	return uiDevice, false
}

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
