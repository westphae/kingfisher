// Package units documents canonical physical units for live.Sample.Values /
// flight DB columns and normalizes IIO driver readings at ingest.
//
// Conventions:
//   - pressure: Pa (pascal)
//   - temperature: °C (column suffix _c)
//   - length / altitude: m
//   - angles: deg (no suffix when the whole device is degrees)
//   - angular rate: deg/s (column suffix _deg_s)
//   - magnetic field: nT (geomag field_*_nt columns) or µT (IIO magn_*, pod mag_*_ut)
package units

import "math"

// GaussToMicroTesla converts kernel IIO magnetometer readings (Gauss) to µT.
const GaussToMicroTesla = 100.0

// NormalizeIIO converts a raw IIO channel reading to canonical units.
// Channel names are the Linux IIO channel ids (e.g. "pressure", "temp").
func NormalizeIIO(channel string, v float64) float64 {
	switch channel {
	case "pressure":
		return v * 1000 // driver reports kPa → Pa
	case "temp":
		return NormalizeTempC(v)
	case "magn_x", "magn_y", "magn_z":
		return v * GaussToMicroTesla // icm20948-mod etc.: Gauss → µT
	default:
		return v
	}
}

// NormalizeTempC converts a temperature reading to °C. IIO convention is
// millidegrees Celsius on in_temp_input and many buffered scans; go-iio
// usually divides on ReadFloat(_input) but the raw×scale path (e.g. some
// ICM-20948 setups) can still deliver m°C.
func NormalizeTempC(v float64) float64 {
	if math.Abs(v) >= 500 {
		return v / 1000
	}
	return v
}

// ColumnForIIO returns the hub/DB column for an IIO channel when it differs
// from store.Sanitize(channel). Empty string means use the mapped column as-is.
func ColumnForIIO(channel string) string {
	switch channel {
	case "pressure":
		return "pressure_pa"
	case "temp":
		return "temp_c"
	default:
		return ""
	}
}
