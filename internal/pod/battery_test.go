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
	}, 850)
	if !learned {
		t.Fatal("expected learned")
	}
	if math.Abs(float64(r.CapacityFullMah-850)) > 0.01 {
		t.Fatalf("full=%v want 850", r.CapacityFullMah)
	}
	if math.Abs(float64(r.CapacityRemainMah-612)) > 1 {
		t.Fatalf("remain=%v want ~612", r.CapacityRemainMah)
	}
	if r.TimeRemainS != -1 {
		t.Fatalf("time_remain=%v want pod value unchanged (-1)", r.TimeRemainS)
	}
}

func TestNormalizeBatteryReading_preservesGauge(t *testing.T) {
	r, learned := NormalizeBatteryReading(wire.BatteryReading{
		CapacityFullMah:   820,
		CapacityRemainMah: 610,
		SocPct:            74,
		TimeRemainS:       3600,
	}, 850)
	if !learned {
		t.Fatal("expected learned")
	}
	if r.CapacityFullMah != 820 || r.CapacityRemainMah != 610 {
		t.Fatalf("unexpected change: %+v", r)
	}
}

func TestNormalizeBatteryReading_unlearnedOmitsCapacity(t *testing.T) {
	r, learned := NormalizeBatteryReading(wire.BatteryReading{
		VoltageV:          4.014,
		CapacityFullMah:   0,
		CapacityRemainMah: 0,
		SocPct:            0,
		CurrentA:          -0.123,
		TimeRemainS:       0,
	}, 850)
	if learned {
		t.Fatal("expected unlearned")
	}
	if r.CapacityFullMah != 0 || r.CapacityRemainMah != 0 || r.SocPct != 0 || r.TimeRemainS != 0 {
		t.Fatalf("capacity fields should be cleared: %+v", r)
	}
	if r.VoltageV != 4.014 {
		t.Fatalf("voltage=%v", r.VoltageV)
	}
}

func TestBatteryGaugeLearned(t *testing.T) {
	if BatteryGaugeLearned(wire.BatteryReading{}, 850) {
		t.Fatal("empty reading should be unlearned")
	}
	if !BatteryGaugeLearned(wire.BatteryReading{SocPct: 1}, 850) {
		t.Fatal("non-zero SOC should be learned")
	}
	if BatteryGaugeLearned(wire.BatteryReading{CapacityFullMah: 1221, SocPct: 50}, 850) {
		t.Fatal("full capacity far from design should be unlearned")
	}
}

func TestNormalizeBatteryReading_wrongFullUnlearned(t *testing.T) {
	_, learned := NormalizeBatteryReading(wire.BatteryReading{
		CapacityFullMah:   1221,
		CapacityRemainMah: 600,
		SocPct:            50,
	}, 850)
	if learned {
		t.Fatal("expected unlearned when full != design")
	}
}

func TestSampleBatteryValues_unlearnedIncludesZeros(t *testing.T) {
	r := newReader(make(chan outboundCmd, 1))
	r.caps[wire.SensorBattery] = wire.SensorCap{ID: wire.SensorBattery}
	_, vals, ok := r.sampleBatteryValues(wire.BatteryReading{
		VoltageV: 4.0, CurrentA: -0.1, PowerW: -0.4,
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
}
