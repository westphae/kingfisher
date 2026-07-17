package ups

import "math"

// Shutdown policy: run-to-floor. On power loss the recorder keeps recording
// (the stored data is the product) and shuts down only when battery
// exhaustion is imminent — a debounced SOC or voltage floor. An optional
// ride timer exists for installations that want a fixed on-battery limit,
// but it is off by default.

// floorDebounce is how many consecutive at-or-below-floor samples are needed
// before a floor fires — one glitched gauge read must never power us off.
const floorDebounce = 3

// TTE estimation needs enough elapsed time and SOC delta for the anchored
// slope to mean anything; below these the estimate is withheld (NaN).
const (
	tteMinElapsedS = 120.0
	tteMinDeltaPct = 0.25
)

// thresholds carries the effective config values for one decide call.
type thresholds struct {
	afterS    float64 // ride timer; <=0 disables (default)
	socFloor  float64 // SOC floor while on battery; <0 disables
	voltFloor float64 // hard voltage floor; <0 disables
}

type reading struct {
	pldOK   bool // PLD line readable
	acOK    bool // external power present (valid only when pldOK)
	gaugeOK bool // gauge readable this sample

	voltageV float64
	socPct   float64
}

type verdict struct {
	shutdown       bool
	reason         string
	onBatteryS     float64
	timeRemainingS float64 // NaN until the discharge slope is estimable
}

type deciderState struct {
	acLostSinceNs int64 // 0 = external power present (or never observed lost)
	socAtLossPct  float64
	haveLossSoc   bool

	lowSocCount  int
	lowVoltCount int

	triggered bool   // latch: the shutdown verdict fires exactly once
	reason    string // set when triggered
}

// decide advances the state machine one sample. Degraded inputs never fire
// the timer or SOC paths: an unreadable PLD freezes the on-battery timer,
// and a dead gauge resets the floor debounce counters. The voltage floor is
// the one check that applies regardless of AC state — a cell at the floor is
// critical even when nominally charging.
func decide(st *deciderState, nowNs int64, r reading, cfg thresholds) verdict {
	v := verdict{timeRemainingS: math.NaN()}

	onBattery := r.pldOK && !r.acOK
	if r.pldOK && r.acOK {
		st.acLostSinceNs = 0
		st.haveLossSoc = false
		st.lowSocCount = 0
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
	// current sense). Anchored average: robust to the 1/256 % quantization.
	if onBattery && r.gaugeOK && st.haveLossSoc {
		delta := st.socAtLossPct - r.socPct
		if v.onBatteryS >= tteMinElapsedS && delta >= tteMinDeltaPct {
			rate := delta / v.onBatteryS // %/s discharged
			floor := math.Max(cfg.socFloor, 0)
			if rem := r.socPct - floor; rem > 0 {
				v.timeRemainingS = rem / rate
			} else {
				v.timeRemainingS = 0
			}
		}
	}

	// Floor debounce counters.
	if r.gaugeOK && cfg.socFloor >= 0 && onBattery && r.socPct <= cfg.socFloor {
		st.lowSocCount++
	} else {
		st.lowSocCount = 0
	}
	if r.gaugeOK && cfg.voltFloor >= 0 && r.voltageV <= cfg.voltFloor {
		st.lowVoltCount++
	} else {
		st.lowVoltCount = 0
	}

	if st.triggered {
		return v
	}

	switch {
	case st.lowVoltCount >= floorDebounce:
		st.triggered, st.reason = true, "voltage_floor"
	case st.lowSocCount >= floorDebounce:
		st.triggered, st.reason = true, "soc_floor"
	case cfg.afterS > 0 && onBattery && v.onBatteryS >= cfg.afterS:
		st.triggered, st.reason = true, "ac_lost_timeout"
	default:
		return v
	}
	v.shutdown = true
	v.reason = st.reason
	return v
}
