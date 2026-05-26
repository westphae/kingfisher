package sensors

import (
	"strconv"

	"github.com/westphae/kingfisher/internal/config"
)

// IsDeviceConfigAttr reports attrs stored in config.Device (not sysfs).
func IsDeviceConfigAttr(attr string) bool {
	return attr == "sample_hz" || attr == "enabled"
}

// OptionsForMaxHz returns Hz preset strings capped at max (for sample_hz UI).
func OptionsForMaxHz(maxHz float64) []string {
	if maxHz <= 0 {
		maxHz = 1000
	}
	if out := optionsFromHzPresets(1, maxHz); len(out) > 0 {
		return out
	}
	return []string{"10"}
}

// ConfigAttrViews returns per-tab settings rows for an on-host IIO device
// (sample rate and enabled), matching pod_* sampling_frequency placement.
func ConfigAttrViews(dev config.Device, maxHz float64) []AttrView {
	opts := OptionsForMaxHz(maxHz)
	hzStr := strconv.FormatFloat(dev.SampleHz, 'f', -1, 64)
	enabled := "false"
	if dev.Enabled {
		enabled = "true"
	}
	return []AttrView{
		{
			Channel:  "",
			Attr:     "sample_hz",
			Value:    hzStr,
			Writable: true,
			Options:  opts,
		},
		{
			Channel:  "",
			Attr:     "enabled",
			Value:    enabled,
			Writable: true,
			Options:  []string{"true", "false"},
		},
	}
}
