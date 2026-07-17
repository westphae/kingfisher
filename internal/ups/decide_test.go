package ups

import (
	"math"
	"testing"
)

const secNs = int64(1e9)

func defaults() thresholds {
	return thresholds{afterS: 0, socFloor: 10, voltFloor: 3.20}
}

func onBatt(soc, volts float64) reading {
	return reading{pldOK: true, acOK: false, gaugeOK: true, socPct: soc, voltageV: volts}
}

func onAC(soc, volts float64) reading {
	return reading{pldOK: true, acOK: true, gaugeOK: true, socPct: soc, voltageV: volts}
}

// Default policy is run-to-floor: an hour on battery with a healthy pack
// must not shut down.
func TestNoTimerByDefault(t *testing.T) {
	st := &deciderState{}
	for i := int64(0); i <= 3600; i++ {
		if v := decide(st, i*secNs, onBatt(80, 3.9), defaults()); v.shutdown {
			t.Fatalf("run-to-floor policy shut down at t=%ds", i)
		}
	}
}

func TestRideTimerFiresAndResets(t *testing.T) {
	cfg := defaults()
	cfg.afterS = 300

	st := &deciderState{}
	// 4 min on battery, then AC returns for one sample, then lost again:
	// the timer must restart from zero.
	for i := int64(0); i < 240; i++ {
		decide(st, i*secNs, onBatt(80, 3.9), cfg)
	}
	decide(st, 240*secNs, onAC(80, 3.9), cfg)
	for i := int64(241); i < 241+299; i++ {
		if v := decide(st, i*secNs, onBatt(80, 3.9), cfg); v.shutdown {
			t.Fatalf("timer did not reset on AC return (fired at t=%ds)", i)
		}
	}
	v := decide(st, (241+300)*secNs, onBatt(80, 3.9), cfg)
	if !v.shutdown || v.reason != "ac_lost_timeout" {
		t.Fatalf("timer did not fire after reset+300s: %+v", v)
	}
}

func TestSocFloorOnBatteryOnlyAndDebounced(t *testing.T) {
	// Low SOC on AC power is a recovering pack, not a dying one.
	st := &deciderState{}
	for i := int64(0); i < 10; i++ {
		if v := decide(st, i*secNs, onAC(5, 3.7), defaults()); v.shutdown {
			t.Fatalf("SOC floor fired while on AC")
		}
	}

	// Two low samples then a recovery must not fire; three consecutive must.
	st = &deciderState{}
	decide(st, 0, onBatt(9, 3.7), defaults())
	decide(st, 1*secNs, onBatt(9, 3.7), defaults())
	if v := decide(st, 2*secNs, onBatt(11, 3.7), defaults()); v.shutdown {
		t.Fatalf("SOC floor fired without %d consecutive samples", floorDebounce)
	}
	decide(st, 3*secNs, onBatt(9, 3.7), defaults())
	decide(st, 4*secNs, onBatt(9, 3.7), defaults())
	v := decide(st, 5*secNs, onBatt(9, 3.7), defaults())
	if !v.shutdown || v.reason != "soc_floor" {
		t.Fatalf("SOC floor did not fire after %d consecutive: %+v", floorDebounce, v)
	}
}

// A cell at the voltage floor is critical even when nominally charging.
func TestVoltageFloorIgnoresACState(t *testing.T) {
	st := &deciderState{}
	decide(st, 0, onAC(50, 3.1), defaults())
	decide(st, 1*secNs, onAC(50, 3.1), defaults())
	v := decide(st, 2*secNs, onAC(50, 3.1), defaults())
	if !v.shutdown || v.reason != "voltage_floor" {
		t.Fatalf("voltage floor did not fire on AC: %+v", v)
	}

	// Single glitch sample must not fire.
	st = &deciderState{}
	decide(st, 0, onBatt(50, 2.0), defaults())
	if v := decide(st, 1*secNs, onBatt(50, 3.9), defaults()); v.shutdown {
		t.Fatalf("single low-voltage glitch fired")
	}
}

func TestDegradedInputsNeverFire(t *testing.T) {
	// Dead gauge: no floor decisions, whatever stale fields say.
	st := &deciderState{}
	r := reading{pldOK: true, acOK: false, gaugeOK: false, socPct: 1, voltageV: 1}
	for i := int64(0); i < 10; i++ {
		if v := decide(st, i*secNs, r, defaults()); v.shutdown {
			t.Fatalf("dead gauge fired a floor")
		}
	}

	// Dead PLD: ride timer must freeze (never fire), voltage floor stays live.
	cfg := defaults()
	cfg.afterS = 5
	st = &deciderState{}
	noPLD := reading{pldOK: false, gaugeOK: true, socPct: 50, voltageV: 3.9}
	for i := int64(0); i < 100; i++ {
		if v := decide(st, i*secNs, noPLD, cfg); v.shutdown {
			t.Fatalf("ride timer fired with dead PLD")
		}
	}
	lowV := reading{pldOK: false, gaugeOK: true, socPct: 50, voltageV: 3.0}
	decide(st, 100*secNs, lowV, cfg)
	decide(st, 101*secNs, lowV, cfg)
	if v := decide(st, 102*secNs, lowV, cfg); !v.shutdown || v.reason != "voltage_floor" {
		t.Fatalf("voltage floor dead with PLD down: %+v", v)
	}
}

func TestShutdownLatchFiresOnce(t *testing.T) {
	st := &deciderState{}
	fired := 0
	for i := int64(0); i < 10; i++ {
		if v := decide(st, i*secNs, onBatt(1, 3.0), defaults()); v.shutdown {
			fired++
		}
	}
	if fired != 1 {
		t.Fatalf("shutdown fired %d times, want exactly 1", fired)
	}
}

func TestNegativeThresholdsDisable(t *testing.T) {
	cfg := thresholds{afterS: 0, socFloor: -1, voltFloor: -1}
	st := &deciderState{}
	for i := int64(0); i < 100; i++ {
		if v := decide(st, i*secNs, onBatt(0, 2.5), cfg); v.shutdown {
			t.Fatalf("disabled thresholds fired")
		}
	}
}

func TestTimeRemainingEstimate(t *testing.T) {
	st := &deciderState{}
	cfg := defaults()

	// Discharge 1% per 100s from 50%: after 300s, SOC 47, rate 0.01 %/s,
	// remaining above the 10% floor = 37% → 3700s.
	var last verdict
	for i := int64(0); i <= 300; i++ {
		soc := 50 - float64(i)/100
		last = decide(st, i*secNs, onBatt(soc, 3.8), cfg)
	}
	if math.IsNaN(last.timeRemainingS) {
		t.Fatalf("TTE still NaN after 300s of measurable discharge")
	}
	if math.Abs(last.timeRemainingS-3700) > 50 {
		t.Fatalf("TTE = %.0fs, want ~3700s", last.timeRemainingS)
	}

	// Too early / too little delta: withheld.
	st = &deciderState{}
	v := decide(st, 0, onBatt(50, 3.8), cfg)
	if !math.IsNaN(v.timeRemainingS) {
		t.Fatalf("TTE emitted with no history")
	}

	// AC return resets the anchor: estimate is withheld again.
	st = &deciderState{}
	for i := int64(0); i <= 300; i++ {
		decide(st, i*secNs, onBatt(50-float64(i)/100, 3.8), cfg)
	}
	decide(st, 301*secNs, onAC(47, 3.8), cfg)
	v = decide(st, 302*secNs, onBatt(47, 3.8), cfg)
	if !math.IsNaN(v.timeRemainingS) {
		t.Fatalf("TTE survived an AC-return reset")
	}
}
