// Package derive computes secondary signals from raw sensor + GPS data:
// pressure altitude, magnetic declination, and AHRS attitude. Each derived
// stream publishes on its own virtual device so the UI and DB treat them
// uniformly.
package derive

import (
	"context"
	"log"
	"math"
	"strconv"
	"time"

	"github.com/westphae/goflying/sensors/bmp280"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/location"
	"github.com/westphae/kingfisher/internal/pod"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/store"
	"github.com/westphae/kingfisher/internal/units"
)

// pressure_source values stored on press_alt (numeric for Values map).
const (
	PressureSourcePod   = 1 // wing pod static_pressure_pa (BMP581)
	PressureSourceCabin = 2 // cabin IIO baro (e.g. bmp280)

	pressAltDeviceName = "press_alt"
	kollsmanAttrName   = "kollsman_inhg"
)

// AltitudeFromHub reads the latest pressure-bearing device snapshot every
// 200 ms and publishes pressure altitude as the "press_alt" virtual device.
// Wing pod static pressure is preferred over cabin IIO. Pressure is Pa;
// pressure altitudes in ft and m. density_alt_ft uses the paired OAT when
// available (pod static_temp_c or cabin temp_c).
func AltitudeFromHub(ctx context.Context, holder *config.Holder, hub *live.Hub, buf *store.Buffer, st *store.Store) {
	t := time.NewTicker(200 * time.Millisecond)
	defer t.Stop()
	reload := holder.Subscribe()
	kollsman := holder.Get().KollsmanInHg()
	logPressAltAttrs(st, kollsman)
	for {
		select {
		case <-ctx.Done():
			return
		case <-reload:
			next := holder.Get().KollsmanInHg()
			if math.Abs(next-kollsman) > 1e-9 {
				kollsman = next
				logPressAltAttrs(st, kollsman)
			}
		case <-t.C:
			snap := hub.SnapshotNow()
			pressPa, source, ok := findPressurePa(snap)
			if !ok {
				continue
			}
			hPa := pressPa / 100.0
			altFt := bmp280.CalcAltitude(hPa)
			altM := altFt * 0.3048
			indicatedFt := IndicatedAltFt(altFt, kollsman)
			indicatedM := indicatedFt * 0.3048
			vals := map[string]float64{
				"pressure_pa":      pressPa,
				"pressure_source":  source,
				"pressure_alt_ft":  altFt,
				"pressure_alt_m":   altM,
				"indicated_alt_ft": indicatedFt,
				"indicated_alt_m":  indicatedM,
				kollsmanAttrName:   kollsman,
			}
			if tempC, ok := findOATC(snap, source); ok {
				vals["density_alt_ft"] = DensityAltFt(altFt, tempC)
			}
			sm := live.Sample{
				Device: pressAltDeviceName,
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

// IndicatedAltFt returns indicated altitude in feet for a pressure altitude
// and altimeter setting in inches of mercury. Standard setting is 29.92 inHg.
func IndicatedAltFt(pressureAltFt, kollsmanInHg float64) float64 {
	return pressureAltFt + (kollsmanInHg-config.DefaultKollsmanInHg)*1000.0
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
	wingBaro := pod.DefaultDeviceName(wire.SensorStatic)
	if sm, have := s.Devices[wingBaro]; have {
		if v, ok := sm.Values[pod.ChStaticP]; ok && validPressurePa(v) {
			return v, PressureSourcePod, true
		}
	}
	for name, sm := range s.Devices {
		if name == "press_alt" || name == pod.DeviceName || name == wingBaro {
			continue
		}
		if pa, ok := cabinPressurePa(sm); ok {
			return pa, PressureSourceCabin, true
		}
	}
	return 0, 0, false
}

func findOATC(s live.Snapshot, source float64) (float64, bool) {
	wingBaro := pod.DefaultDeviceName(wire.SensorStatic)
	switch source {
	case PressureSourcePod:
		if sm, ok := s.Devices[wingBaro]; ok {
			return sampleTempC(sm, pod.ChStaticTemp)
		}
	case PressureSourceCabin:
		for name, sm := range s.Devices {
			if name == "press_alt" || name == pod.DeviceName || name == wingBaro {
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

func logPressAltAttrs(st *store.Store, kollsman float64) {
	if st == nil {
		return
	}
	if err := st.LogAttrs(pressAltDeviceName, location.Hub, []store.AttrRecord{{
		Attr:  kollsmanAttrName,
		Value: strconv.FormatFloat(kollsman, 'f', 2, 64),
	}}); err != nil {
		log.Printf("derive: log press_alt attrs: %v", err)
	}
}
