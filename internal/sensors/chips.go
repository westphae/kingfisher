package sensors

import "strings"

// ChipAttr is one entry in the per-chip fallback table. It supplies
// knowledge the IIO driver doesn't publish at runtime — the enumerated
// values some chips accept but never expose via an `_available` sibling,
// or attributes whose sysfs mode lies about writability (e.g. BMP280's
// in_pressure_scale, which is mode 0664 but rejects writes because the
// scale is derived from the chip's factory calibration registers).
type ChipAttr struct {
	// Options is the legal enumeration per the datasheet. Empty means we
	// don't claim to know — the UI falls back to a free-text input.
	Options []string
	// ReadOnly forces Writable=false in the registry view even if the
	// sysfs mode bits suggest otherwise. Use sparingly: only when the
	// driver actually rejects writes.
	ReadOnly bool
}

// chipFallbacks is keyed by lowercased chip name (matching the kernel's
// `name` file), then channel (use "" for device-level attrs), then attr
// suffix (the same suffix passed to ChannelAttr/Attr). Add entries here
// as new sensors are added to the airframe.
//
// References:
//   - BMP280 datasheet rev 1.20, Table 5: pressure oversampling 1..16x.
//     in_pressure_scale and in_temp_scale are derived from calibration
//     registers and not user-tunable in the in-tree driver.
var chipFallbacks = map[string]map[string]map[string]ChipAttr{
	"bmp280": {
		"pressure": {
			"oversampling_ratio": {Options: []string{"1", "2", "4", "8", "16"}},
			"scale":              {ReadOnly: true},
		},
		"temp": {
			"oversampling_ratio": {Options: []string{"1", "2", "4", "8", "16"}},
			"scale":              {ReadOnly: true},
		},
	},
}

// ChipFallback returns the fallback entry for a given (chip, channel, attr)
// triple, or zero-value+false if none is registered.
func ChipFallback(chip, channel, attr string) (ChipAttr, bool) {
	byCh, ok := chipFallbacks[strings.ToLower(chip)]
	if !ok {
		return ChipAttr{}, false
	}
	byAttr, ok := byCh[channel]
	if !ok {
		return ChipAttr{}, false
	}
	v, ok := byAttr[attr]
	return v, ok
}
