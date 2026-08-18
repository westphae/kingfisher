package pod

import (
	"math"
	"testing"

	"github.com/westphae/kingfisher/internal/pod/wire"
)

func TestNormalizeBatteryReading_zeroFullUsesDesign(t *testing.T) {
	r, learned := NormalizeBatteryReading(wire.BatteryReading{
		CapacityFullMah:   0,
		CapacityRemainMah: 0,
		SocPct:            72,
		CurrentA:          -0.15,
		TimeRemainS:       -1,
	}, 850, 0)
	if learned {
		t.Fatal("SOC without a matching full-capacity is unlearned")
	}
	if math.Abs(float64(r.CapacityFullMah-850)) > 0.01 {
		t.Fatalf("full=%v want design 850", r.CapacityFullMah)
	}
	if r.CapacityRemainMah != 0 || r.SocPct != 0 {
		t.Fatalf("untrusted SOC/remain should be cleared: %+v", r)
	}
}

func TestNormalizeBatteryReading_preservesGauge(t *testing.T) {
	r, learned := NormalizeBatteryReading(wire.BatteryReading{
		CapacityFullMah:   820,
		CapacityRemainMah: 610,
		SocPct:            74,
		TimeRemainS:       3600,
	}, 850, 0)
	if !learned {
		t.Fatal("expected learned")
	}
	if r.CapacityFullMah != 820 || r.CapacityRemainMah != 610 {
		t.Fatalf("unexpected change: %+v", r)
	}
}

func TestNormalizeBatteryReading_unlearnedUsesDesignFull(t *testing.T) {
	r, learned := NormalizeBatteryReading(wire.BatteryReading{
		VoltageV:          4.014,
		CapacityFullMah:   0,
		CapacityRemainMah: 0,
		SocPct:            0,
		CurrentA:          -0.123,
		TimeRemainS:       0,
	}, 2000, 1840)
	if learned {
		t.Fatal("expected unlearned")
	}
	if math.Abs(float64(r.CapacityFullMah-1840)) > 0.01 {
		t.Fatalf("full=%v want last-learned 1840", r.CapacityFullMah)
	}
	if r.CapacityRemainMah != 0 || r.SocPct != 0 || r.TimeRemainS != 0 {
		t.Fatalf("SOC/remain/time should stay hidden: %+v", r)
	}
	if r.VoltageV != 4.014 {
		t.Fatalf("voltage=%v", r.VoltageV)
	}
}

func TestBatteryGaugeLearned(t *testing.T) {
	if BatteryGaugeLearned(wire.BatteryReading{}, 850) {
		t.Fatal("empty reading should be unlearned")
	}
	if BatteryGaugeLearned(wire.BatteryReading{SocPct: 1}, 850) {
		t.Fatal("SOC without matching FCC should be unlearned")
	}
	if BatteryGaugeLearned(wire.BatteryReading{CapacityFullMah: 1221, SocPct: 50}, 850) {
		t.Fatal("full capacity far from design should be unlearned")
	}
	if BatteryGaugeLearned(wire.BatteryReading{CapacityFullMah: 1340, SocPct: 50}, 2000) {
		t.Fatal("factory G1A 1340 mAh vs 2000 pack should be unlearned")
	}
	if !BatteryGaugeLearned(wire.BatteryReading{CapacityFullMah: 1600, SocPct: 50}, 2000) {
		t.Fatal("aged 2000 mAh pack at 1600 should still count as learned")
	}
}

func TestNormalizeBatteryReading_wrongFullFallsBackToDesign(t *testing.T) {
	r, learned := NormalizeBatteryReading(wire.BatteryReading{
		CapacityFullMah:   1221,
		CapacityRemainMah: 600,
		SocPct:            50,
	}, 850, 0)
	if learned {
		t.Fatal("expected unlearned when full != design")
	}
	if math.Abs(float64(r.CapacityFullMah-850)) > 0.01 {
		t.Fatalf("full=%v want design 850", r.CapacityFullMah)
	}
	if r.SocPct != 0 || r.CapacityRemainMah != 0 {
		t.Fatalf("untrusted SOC/remain should be cleared: %+v", r)
	}
}

func TestSampleBatteryValues_unlearnedIncludesZeros(t *testing.T) {
	r := newReader(make(chan outboundCmd, 1))
	r.caps[wire.SensorBattery] = wire.SensorCap{ID: wire.SensorBattery}
	_, vals, ok := r.sampleBatteryValues(wire.BatteryReading{
		VoltageV: 4.0, CurrentA: -0.1, PowerW: -0.4, CapacityFullMah: 2000,
	}, false)
	if !ok {
		t.Fatal("expected ok")
	}
	for _, k := range []string{
		ChBatteryCapRm, ChBatteryCapFull, ChBatterySOC, ChBatteryTime, ChBatteryLearned,
	} {
		if _, exists := vals[k]; !exists {
			t.Fatalf("missing %s", k)
		}
	}
	if vals[ChBatteryLearned] != 0 || vals[ChBatterySOC] != 0 {
		t.Fatalf("unexpected: %+v", vals)
	}
	if vals[ChBatteryCapFull] != 2000 {
		t.Fatalf("full=%v want design fallback 2000", vals[ChBatteryCapFull])
	}
}
