// Package derive computes secondary signals from raw sensor + GPS data:
// pressure altitude, magnetic declination, and AHRS attitude. Each derived
// stream publishes on its own virtual device so the UI and DB treat them
// uniformly.
package derive

import (
	"context"
	"math"
	"time"

	"github.com/westphae/goflying/sensors/bmp280"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
	"github.com/westphae/kingfisher/internal/store"
	"github.com/westphae/kingfisher/internal/units"
)

// pressure_source values stored on press_alt (numeric for Values map).
const (
	PressureSourcePod   = 1 // wing pod static_pressure_pa (BMP581)
	PressureSourceCabin = 2 // cabin IIO baro (e.g. bmp280)
)

// AltitudeFromHub reads the latest pressure-bearing device snapshot every
// 200 ms and publishes pressure altitude as the "press_alt" virtual device.
// Wing pod static pressure is preferred over cabin IIO. Pressure is Pa;
// pressure altitudes in ft and m. density_alt_ft uses the paired OAT when
// available (pod static_temp_c or cabin temp_c).
func AltitudeFromHub(ctx context.Context, hub *live.Hub, buf *store.Buffer) {
	t := time.NewTicker(200 * time.Millisecond)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			snap := hub.SnapshotNow()
			pressPa, source, ok := findPressurePa(snap)
			if !ok {
				continue
			}
			hPa := pressPa / 100.0
			altFt := bmp280.CalcAltitude(hPa)
			altM := altFt * 0.3048
			vals := map[string]float64{
				"pressure_pa":     pressPa,
				"pressure_source": source,
				"pressure_alt_ft": altFt,
				"pressure_alt_m":  altM,
			}
			if tempC, ok := findOATC(snap, source); ok {
				vals["density_alt_ft"] = DensityAltFt(altFt, tempC)
			}
			sm := live.Sample{
				Device: "press_alt",
				TsNs:   time.Now().UnixNano(),
				Values: vals,
			}
			hub.Publish(sm)
			if buf != nil {
				buf.Append(sm)
			}
		}
	}
}

// DensityAltFt returns density altitude in feet from pressure altitude (ft)
// and outside air temperature (°C), using ISA temperature at PA and the
// standard 120 ft/°C correction above/below ISA.
func DensityAltFt(pressureAltFt, oatC float64) float64 {
	isa := isaTempCAtPAFt(pressureAltFt)
	return pressureAltFt + 120.0*(oatC-isa)
}

func isaTempCAtPAFt(paFt float64) float64 {
	return 15.0 - 2.0*(paFt/1000.0)
}

// findPressurePa returns barometric pressure in Pa and a pressure_source code.
// Prefers pod static_pressure_pa over cabin IIO pressure_pa.
func findPressurePa(s live.Snapshot) (pa float64, source float64, ok bool) {
	if sm, have := s.Devices[pod.DeviceName]; have {
		if v, ok := sm.Values[pod.ChStaticP]; ok && validPressurePa(v) {
			return v, PressureSourcePod, true
		}
	}
	for name, sm := range s.Devices {
		if name == "press_alt" || name == pod.DeviceName {
			continue
		}
		if pa, ok := cabinPressurePa(sm); ok {
			return pa, PressureSourceCabin, true
		}
	}
	return 0, 0, false
}

func findOATC(s live.Snapshot, source float64) (float64, bool) {
	switch source {
	case PressureSourcePod:
		if sm, ok := s.Devices[pod.DeviceName]; ok {
			return sampleTempC(sm, pod.ChStaticTemp)
		}
	case PressureSourceCabin:
		for name, sm := range s.Devices {
			if name == "press_alt" || name == pod.DeviceName {
				continue
			}
			if _, ok := cabinPressurePa(sm); ok {
				return sampleTempC(sm, "temp_c", "temp")
			}
		}
	}
	return 0, false
}

func sampleTempC(sm live.Sample, keys ...string) (float64, bool) {
	for _, k := range keys {
		if v, ok := sm.Values[k]; ok && !math.IsNaN(v) {
			v = units.NormalizeTempC(v)
			if validTempC(v) {
				return v, true
			}
		}
	}
	return 0, false
}

func cabinPressurePa(sm live.Sample) (float64, bool) {
	if v, ok := sm.Values["pressure_pa"]; ok && validPressurePa(v) {
		return v, true
	}
	// Legacy columns before SI normalization.
	if v, ok := sm.Values["pressure"]; ok && validPressurePa(v*1000) {
		return v * 1000, true // kPa
	}
	if v, ok := sm.Values["press"]; ok && validPressurePa(v*1000) {
		return v * 1000, true
	}
	if v, ok := sm.Values["pressure_hpa"]; ok && validPressurePa(v*100) {
		return v * 100, true // hPa
	}
	return 0, false
}

func validPressurePa(v float64) bool {
	return !math.IsNaN(v) && v > 10_000 // reject hPa/kPa mistaken for Pa
}

func validTempC(v float64) bool {
	return !math.IsNaN(v) && v > -100 && v < 200
}
