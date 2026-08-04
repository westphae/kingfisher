package ups

import "math"

// Kingfisher does not manage power. The x120x kernel driver owns the UPS:
// it reports capacity_level=Critical on battery, UPower turns that into
// CriticalPowerAction=PowerOff at PercentageAction, and systemd stops this
// service with TimeoutStopSec to let the DB close cleanly. Nothing here may
// power the machine off — this file only derives the recorded values.
//
// (The manual poweroff button in the web UI is a separate path and is
// unaffected; see internal/web/server.go.)

// TTE estimation needs enough elapsed time and SOC delta for the anchored
// slope to mean anything; below these the estimate is withheld (NaN).
const (
	tteMinElapsedS = 120.0
	tteMinDeltaPct = 0.25
)

type reading struct {
	pldOK   bool // AC state readable
	acOK    bool // external power present (valid only when pldOK)
	gaugeOK bool // gauge readable this sample

	voltageV float64
	socPct   float64
}

type verdict struct {
	onBatteryS     float64
	timeRemainingS float64 // NaN until the discharge slope is estimable
}

type deciderState struct {
	acLostSinceNs int64 // 0 = external power present (or never observed lost)
	socAtLossPct  float64
	haveLossSoc   bool
}

// decide advances the state machine one sample. Degraded inputs are handled
// conservatively: an unreadable AC line freezes the on-battery timer, and a
// dead gauge withholds the time-to-empty estimate.
//
// poweroffFloorPct is the SOC at which the driver/UPower will cut power. It
// is used only to make "time remaining" mean time-until-poweroff rather than
// time-until-empty; it triggers nothing here.
func decide(st *deciderState, nowNs int64, r reading, poweroffFloorPct float64) verdict {
	v := verdict{timeRemainingS: math.NaN()}

	onBattery := r.pldOK && !r.acOK
	if r.pldOK && r.acOK {
		st.acLostSinceNs = 0
		st.haveLossSoc = false
	}
	if onBattery {
		if st.acLostSinceNs == 0 {
			st.acLostSinceNs = nowNs
			st.haveLossSoc = false
		}
		v.onBatteryS = float64(nowNs-st.acLostSinceNs) / 1e9
		if r.gaugeOK && !st.haveLossSoc {
			st.socAtLossPct = r.socPct
			st.haveLossSoc = true
		}
	}

	// Time-to-empty from the SOC slope since power loss (the MAX17040 has no
	// current sense). Anchored average: robust to the 1/256 % quantization
	// that the raw gauge read preserves and sysfs `capacity` does not.
	if onBattery && r.gaugeOK && st.haveLossSoc {
		delta := st.socAtLossPct - r.socPct
		if v.onBatteryS >= tteMinElapsedS && delta >= tteMinDeltaPct {
			rate := delta / v.onBatteryS // %/s discharged
			floor := math.Max(poweroffFloorPct, 0)
			if rem := r.socPct - floor; rem > 0 {
				v.timeRemainingS = rem / rate
			} else {
				v.timeRemainingS = 0
			}
		}
	}
	return v
}
