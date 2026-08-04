package ups

import (
	"math"
	"testing"
)

const secNs = int64(1e9)

// deciderState uses 0 as its "not on battery" sentinel, so tests must run on
// realistic wall-clock nanoseconds; a literal t=0 would read as unset.
const baseNs = int64(1_750_000_000) * secNs

// Mirrors the default UPS.PoweroffSocEffective(), i.e. UPower PercentageAction.
const testFloor = 5.0

func onBatt(soc, volts float64) reading {
	return reading{pldOK: true, acOK: false, gaugeOK: true, socPct: soc, voltageV: volts}
}

func onAC(soc, volts float64) reading {
	return reading{pldOK: true, acOK: true, gaugeOK: true, socPct: soc, voltageV: volts}
}

// The whole point of the x120x migration: kingfisher records, it never powers
// the machine off. A verdict carries no shutdown signal at all, so no amount
// of abuse — flat pack, dead cell voltage, hours on battery — can produce one.
// If someone reintroduces a shutdown field, this stops compiling, which is the
// intent.
func TestVerdictCarriesNoShutdownSignal(t *testing.T) {
	st := &deciderState{}
	for i := int64(0); i <= 7200; i++ {
		v := decide(st, baseNs+i*secNs, onBatt(0, 2.5), testFloor)
		_ = v.onBatteryS
		_ = v.timeRemainingS
	}
}

func TestOnBatteryTimerAccumulatesAndResets(t *testing.T) {
	st := &deciderState{}
	var v verdict
	for i := int64(0); i <= 240; i++ {
		v = decide(st, baseNs+i*secNs, onBatt(80, 3.9), testFloor)
	}
	if math.Abs(v.onBatteryS-240) > 0.001 {
		t.Fatalf("on_battery_s = %.3f, want 240", v.onBatteryS)
	}

	// AC returns: the timer must zero, then restart from the new loss.
	v = decide(st, baseNs+241*secNs, onAC(80, 3.9), testFloor)
	if v.onBatteryS != 0 {
		t.Fatalf("on_battery_s = %.3f on AC, want 0", v.onBatteryS)
	}
	v = decide(st, baseNs+250*secNs, onBatt(80, 3.9), testFloor)
	if v.onBatteryS != 0 {
		t.Fatalf("first sample after loss should anchor at 0, got %.3f", v.onBatteryS)
	}
	v = decide(st, baseNs+260*secNs, onBatt(80, 3.9), testFloor)
	if math.Abs(v.onBatteryS-10) > 0.001 {
		t.Fatalf("on_battery_s = %.3f after restart, want 10", v.onBatteryS)
	}
}

// An unreadable AC line (driver not loaded) must freeze the timer rather than
// silently claim we are on battery.
func TestDeadACLineFreezesTimer(t *testing.T) {
	st := &deciderState{}
	noPLD := reading{pldOK: false, gaugeOK: true, socPct: 50, voltageV: 3.9}
	for i := int64(0); i < 100; i++ {
		if v := decide(st, baseNs+i*secNs, noPLD, testFloor); v.onBatteryS != 0 {
			t.Fatalf("timer advanced with no AC state: %.3f", v.onBatteryS)
		}
	}
}

func TestTimeRemainingEstimate(t *testing.T) {
	st := &deciderState{}

	// Discharge 1% per 100s from 50%: after 300s SOC 47, rate 0.01 %/s,
	// remaining above the 5% poweroff floor = 42% → 4200s.
	var last verdict
	for i := int64(0); i <= 300; i++ {
		last = decide(st, baseNs+i*secNs, onBatt(50-float64(i)/100, 3.8), testFloor)
	}
	if math.IsNaN(last.timeRemainingS) {
		t.Fatalf("TTE still NaN after 300s of measurable discharge")
	}
	if math.Abs(last.timeRemainingS-4200) > 50 {
		t.Fatalf("TTE = %.0fs, want ~4200s", last.timeRemainingS)
	}

	// Too early / too little delta: withheld.
	st = &deciderState{}
	if v := decide(st, baseNs, onBatt(50, 3.8), testFloor); !math.IsNaN(v.timeRemainingS) {
		t.Fatalf("TTE emitted with no history")
	}

	// AC return resets the anchor: estimate is withheld again.
	st = &deciderState{}
	for i := int64(0); i <= 300; i++ {
		decide(st, baseNs+i*secNs, onBatt(50-float64(i)/100, 3.8), testFloor)
	}
	decide(st, baseNs+301*secNs, onAC(47, 3.8), testFloor)
	if v := decide(st, baseNs+302*secNs, onBatt(47, 3.8), testFloor); !math.IsNaN(v.timeRemainingS) {
		t.Fatalf("TTE survived an AC-return reset")
	}
}

// Below the poweroff floor the estimate clamps to zero rather than going
// negative — UPower should already have acted by then.
func TestTimeRemainingClampsAtFloor(t *testing.T) {
	st := &deciderState{}
	var last verdict
	for i := int64(0); i <= 300; i++ {
		last = decide(st, baseNs+i*secNs, onBatt(6-float64(i)/100, 3.4), testFloor)
	}
	if last.timeRemainingS != 0 {
		t.Fatalf("TTE = %.1f below floor, want 0", last.timeRemainingS)
	}
}

// A dead gauge must withhold the estimate, not emit a stale or zero one.
func TestDeadGaugeWithholdsTTE(t *testing.T) {
	st := &deciderState{}
	r := reading{pldOK: true, acOK: false, gaugeOK: false, socPct: 1, voltageV: 1}
	for i := int64(0); i < 300; i++ {
		if v := decide(st, baseNs+i*secNs, r, testFloor); !math.IsNaN(v.timeRemainingS) {
			t.Fatalf("dead gauge produced a TTE: %.1f", v.timeRemainingS)
		}
	}
}
