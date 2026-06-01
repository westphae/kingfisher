package pod

import (
	"math"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/pod/wire"
)

// gaugeFullMatchesDesign is true when the full-capacity register matches design (±15%).
func gaugeFullMatchesDesign(fullMah float32, designMah uint16) bool {
	if designMah == 0 || fullMah <= 0 {
		return false
	}
	design := float32(designMah)
	ratio := fullMah / design
	return ratio >= 0.85 && ratio <= 1.15
}

// BatteryGaugeLearned reports whether capacity/SOC are trustworthy for display.
// A non-zero full register that does not match configured design is treated as
// unlearned (stale gauge data until reprogrammed).
func BatteryGaugeLearned(r wire.BatteryReading, designMah uint16) bool {
	if r.CapacityFullMah <= 0 && r.CapacityRemainMah <= 0 && r.SocPct <= 0 {
		return false
	}
	if r.CapacityFullMah > 0 && designMah > 0 && !gaugeFullMatchesDesign(r.CapacityFullMah, designMah) {
		return false
	}
	return r.CapacityFullMah > 0 || r.CapacityRemainMah > 0 || r.SocPct > 0
}

// NormalizeBatteryReading adjusts learned gauge readings (design-capacity fallback,
// derived remain/time). When unlearned, capacity/SOC/time are left at zero for
// hub/DB; battery_gauge_learned flags the state for the UI.
func NormalizeBatteryReading(r wire.BatteryReading, designMah uint16) (wire.BatteryReading, bool) {
	if !BatteryGaugeLearned(r, designMah) {
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
