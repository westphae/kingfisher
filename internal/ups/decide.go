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

	// tteMinFracUsed guards the divide that calibrates the profile's scale.
	// Near the top of the curve a real SOC move maps to a tiny change in
	// runtime fraction, and dividing by it would amplify gauge noise into a
	// wild estimate.
	tteMinFracUsed = 0.005
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

	// Time-to-empty via the runtime profile in tte.go (the MAX17040 has no
	// current sense, so this is all derived from SOC). Position on the curve
	// gives the shape; elapsed time since power loss calibrates its scale to
	// this discharge's actual load. The 1/256 % resolution of the raw gauge
	// read matters here — sysfs `capacity` is integer-only and would quantise
	// the consumed-fraction term badly early on.
	if onBattery && r.gaugeOK {
		floor := math.Max(poweroffFloorPct, 0)
		switch {
		case r.socPct <= floor:
			// At or past the poweroff point there is no time left to report,
			// and the profile is too flat down here to calibrate against.
			// Say zero rather than withholding: "—" would read as "unknown"
			// at the moment the answer is most certain.
			v.timeRemainingS = 0
		case st.haveLossSoc && v.onBatteryS >= tteMinElapsedS &&
			st.socAtLossPct-r.socPct >= tteMinDeltaPct:
			fStart := runtimeFrac(st.socAtLossPct)
			fNow := runtimeFrac(r.socPct)
			if used := fStart - fNow; used > tteMinFracUsed {
				total := v.onBatteryS / used // seconds for a full discharge at this load
				if rem := fNow - runtimeFrac(floor); rem > 0 {
					v.timeRemainingS = total * rem
				} else {
					v.timeRemainingS = 0
				}
			}
		}
	}
	return v
}
