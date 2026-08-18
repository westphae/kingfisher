package pod

import (
	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/pod/wire"
)

// gaugeFullMatchesDesign is true when the full-capacity register is in the
// same ballpark as design (70–120%). Factory G1A Qmax is ~1340 mAh, which
// fails this check against a 2000 or 750 pack until we restore Qmax.
func gaugeFullMatchesDesign(fullMah float32, designMah uint16) bool {
	if designMah == 0 || fullMah <= 0 {
		return false
	}
	design := float32(designMah)
	ratio := fullMah / design
	return ratio >= 0.70 && ratio <= 1.20
}

// BatteryGaugeLearned reports whether capacity/SOC are trustworthy for display.
// FullChargeCapacity must be in the same ballpark as design; a factory G1A
// leftover (~1340 mAh) or a zero FCC is unlearned even if SOC is non-zero.
func BatteryGaugeLearned(r wire.BatteryReading, designMah uint16) bool {
	if r.CapacityFullMah <= 0 {
		return false
	}
	if designMah > 0 && !gaugeFullMatchesDesign(r.CapacityFullMah, designMah) {
		return false
	}
	return true
}

func fallbackFullMah(designMah, learnedQmax uint16) float32 {
	if designMah == 0 {
		designMah = config.DefaultPodBatteryCapacityMah
	}
	if learnedQmax > 0 && gaugeFullMatchesDesign(float32(learnedQmax), designMah) {
		return float32(learnedQmax)
	}
	return float32(designMah)
}

// NormalizeBatteryReading adjusts learned gauge readings (design-capacity fallback,
// derived remain from SOC when missing). When unlearned, SOC/remain/time are left
// at zero for hub/DB; Full Capacity falls back to last-learned Qmax or design so
// the UI is not blank and sleep policy is not driven by a factory 1340 mAh leftover.
func NormalizeBatteryReading(r wire.BatteryReading, designMah uint16, learnedQmax uint16) (wire.BatteryReading, bool) {
	if !BatteryGaugeLearned(r, designMah) {
		r.CapacityFullMah = fallbackFullMah(designMah, learnedQmax)
		r.CapacityRemainMah = 0
		r.SocPct = 0
		r.TimeRemainS = 0
		return r, false
	}
	if designMah == 0 {
		designMah = config.DefaultPodBatteryCapacityMah
	}
	if r.CapacityFullMah <= 0 {
		r.CapacityFullMah = fallbackFullMah(designMah, learnedQmax)
	}
	if r.CapacityRemainMah <= 0 && r.SocPct > 0 && r.CapacityFullMah > 0 {
		r.CapacityRemainMah = r.SocPct / 100 * r.CapacityFullMah
	}
	return r, true
}

// NormalizeReading applies pod-specific fixes to wire readings before publish.
func NormalizeReading(rd wire.Reading, designMah uint16) wire.Reading {
	if br, ok := rd.(wire.BatteryReading); ok {
		normalized, _ := NormalizeBatteryReading(br, designMah, 0)
		return normalized
	}
	return rd
}
