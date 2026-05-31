package pod

import (
	"math"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/pod/wire"
)

// BatteryGaugeLearned reports whether the BQ27441 has non-zero capacity/SOC data.
func BatteryGaugeLearned(r wire.BatteryReading) bool {
	return r.CapacityFullMah > 0 || r.CapacityRemainMah > 0 || r.SocPct > 0
}

// NormalizeBatteryReading adjusts learned gauge readings (design-capacity fallback,
// derived remain/time). When unlearned, capacity/SOC/time are left at zero for
// hub/DB; battery_gauge_learned flags the state for the UI.
func NormalizeBatteryReading(r wire.BatteryReading, designMah uint16) (wire.BatteryReading, bool) {
	if !BatteryGaugeLearned(r) {
		r.CapacityFullMah = 0
		r.CapacityRemainMah = 0
		r.SocPct = 0
		r.TimeRemainS = 0
		return r, false
	}
	if designMah == 0 {
		designMah = config.DefaultPodBatteryCapacityMah
	}
	design := float32(designMah)
	if r.CapacityFullMah <= 0 {
		r.CapacityFullMah = design
	}
	if r.CapacityRemainMah <= 0 && r.SocPct > 0 && r.CapacityFullMah > 0 {
		r.CapacityRemainMah = r.SocPct / 100 * r.CapacityFullMah
	}
	if r.TimeRemainS <= 0 && r.CurrentA < -0.001 && r.CapacityRemainMah > 0 {
		r.TimeRemainS = (r.CapacityRemainMah / float32(math.Abs(float64(r.CurrentA)))) * 3600
	}
	return r, true
}

// NormalizeReading applies pod-specific fixes to wire readings before publish.
func NormalizeReading(rd wire.Reading, designMah uint16) wire.Reading {
	if br, ok := rd.(wire.BatteryReading); ok {
		normalized, _ := NormalizeBatteryReading(br, designMah)
		return normalized
	}
	return rd
}
