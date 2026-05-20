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
	"github.com/westphae/kingfisher/internal/store"
)

// AltitudeFromHub reads the latest pressure-bearing device snapshot every
// 200 ms and publishes pressure altitude as the "press_alt" virtual device.
// Pressure is expected in hPa. Output altitude is meters (converted from
// the feet that bmp280.CalcAltitude returns).
func AltitudeFromHub(ctx context.Context, hub *live.Hub, buf *store.Buffer) {
	t := time.NewTicker(200 * time.Millisecond)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			snap := hub.SnapshotNow()
			press, ok := findPressureHPa(snap)
			if !ok {
				continue
			}
			altFt := bmp280.CalcAltitude(press)
			altM := altFt * 0.3048
			sm := live.Sample{
				Device: "press_alt",
				TsNs:   time.Now().UnixNano(),
				Values: map[string]float64{
					"pressure_hpa": press,
					"alt_ft":       altFt,
					"alt_m":        altM,
				},
			}
			hub.Publish(sm)
			if buf != nil {
				buf.Append(sm)
			}
		}
	}
}

// findPressureHPa scans the snapshot for a "pressure" channel and returns
// its value in hPa. IIO reports baro pressure in kPa; we multiply by 10.
func findPressureHPa(s live.Snapshot) (float64, bool) {
	for _, sm := range s.Devices {
		if v, ok := sm.Values["pressure"]; ok && !math.IsNaN(v) && v > 0 {
			// kPa → hPa
			return v * 10.0, true
		}
		if v, ok := sm.Values["press"]; ok && !math.IsNaN(v) && v > 0 {
			return v * 10.0, true
		}
		// Some drivers expose mbar directly via "pressure_hpa" override.
		if v, ok := sm.Values["pressure_hpa"]; ok && !math.IsNaN(v) && v > 0 && sm.Device != "press_alt" {
			return v, true
		}
	}
	return 0, false
}
