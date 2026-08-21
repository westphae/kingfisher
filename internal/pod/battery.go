package pod

import (
	"github.com/westphae/kingfisher/internal/pod/wire"
)

// gaugeFullMatchesDesign is true when the full-capacity register is in the
// same ballpark as the configured pack (70–120%). Factory G1A FCC is ~1340 mAh,
// which fails this check against a 2000 or 750 pack until Impedance Track
// has a matching Design Capacity and a real cycle.
func gaugeFullMatchesDesign(fullMah float32, designMah uint16) bool {
	if designMah == 0 || fullMah <= 0 {
		return false
	}
	design := float32(designMah)
	ratio := fullMah / design
	return ratio >= 0.70 && ratio <= 1.20
}

// BatteryGaugeLearned reports whether capacity/SOC are trustworthy for display.
// FullChargeCapacity must be in the same ballpark as the configured pack; a
// factory G1A leftover (~1340 mAh) or a zero FCC is unlearned even if SOC is
// non-zero. Compare against the pack in config, not chip 0x3C — a leftover
// 1340/1340 pair would otherwise look learned.
func BatteryGaugeLearned(r wire.BatteryReading, designMah uint16) bool {
	if r.CapacityFullMah <= 0 {
		return false
	}
	if designMah > 0 && !gaugeFullMatchesDesign(r.CapacityFullMah, designMah) {
		return false
	}
	return true
}

// NormalizeBatteryReading keeps chip FullChargeCapacity as-is. When unlearned,
// SOC/remain/time are zeroed for hub/DB so the UI does not treat a factory
// leftover as a real state of charge. Full is never substituted with a Pi-side
// last-learned or design value.
func NormalizeBatteryReading(r wire.BatteryReading, designMah uint16) (wire.BatteryReading, bool) {
	if !BatteryGaugeLearned(r, designMah) {
		r.CapacityRemainMah = 0
		r.SocPct = 0
		r.TimeRemainS = 0
		return r, false
	}
	if r.CapacityRemainMah <= 0 && r.SocPct > 0 && r.CapacityFullMah > 0 {
		r.CapacityRemainMah = r.SocPct / 100 * r.CapacityFullMah
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
