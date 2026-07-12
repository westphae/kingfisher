package system

import (
	"context"
	"errors"
	"math"
	"testing"
)

func approx(a, b float64) bool { return math.Abs(a-b) < 1e-6 }

func TestParseLoadAvg(t *testing.T) {
	l1, l5, l15, ok := parseLoadAvg("1.37 1.34 1.29 1/207 30004")
	if !ok || !approx(l1, 1.37) || !approx(l5, 1.34) || !approx(l15, 1.29) {
		t.Fatalf("got %v %v %v ok=%v", l1, l5, l15, ok)
	}
	if _, _, _, ok := parseLoadAvg("garbage"); ok {
		t.Fatal("expected failure on short input")
	}
}

func TestParseUptime(t *testing.T) {
	up, ok := parseUptime("5449.60 20327.44")
	if !ok || !approx(up, 5449.60) {
		t.Fatalf("got %v ok=%v", up, ok)
	}
}

func TestParseMeminfo(t *testing.T) {
	const s = `MemTotal:        8255888 kB
MemFree:         6034704 kB
MemAvailable:    7546832 kB
SwapTotal:       1000000 kB
SwapFree:         750000 kB`
	m := parseMeminfo(s)
	if m["MemTotal"] != 8255888 || m["MemAvailable"] != 7546832 {
		t.Fatalf("mem fields: %v", m)
	}
	if m["SwapTotal"] != 1000000 || m["SwapFree"] != 750000 {
		t.Fatalf("swap fields: %v", m)
	}
}

func TestParseCPUStat(t *testing.T) {
	idle, total, ok := parseCPUStat("cpu  53393 0 28116 2032745 426 0 961 0 0 0\ncpu0 1 2 3 4")
	if !ok {
		t.Fatal("parse failed")
	}
	if idle != 2032745+426 {
		t.Fatalf("idle=%d", idle)
	}
	wantTotal := int64(53393 + 0 + 28116 + 2032745 + 426 + 0 + 961)
	if total != wantTotal {
		t.Fatalf("total=%d want %d", total, wantTotal)
	}
}

func TestParseSelfRSSkB(t *testing.T) {
	kb, ok := parseSelfRSSkB("Name:\tkingfisher\nVmRSS:\t    6448 kB\nThreads:\t9")
	if !ok || kb != 6448 {
		t.Fatalf("got %d ok=%v", kb, ok)
	}
}

func TestParseMilliC(t *testing.T) {
	c, ok := parseMilliC("51250")
	if !ok || !approx(c, 51.25) {
		t.Fatalf("got %v ok=%v", c, ok)
	}
}

func TestParseThrottled(t *testing.T) {
	m, ok := parseThrottled("throttled=0x0")
	if !ok || m["throttled_bits"] != 0 || m["undervolt_now"] != 0 || m["undervolt_since_boot"] != 0 {
		t.Fatalf("clear case: %v ok=%v", m, ok)
	}
	// Undervolt now + throttling occurred since boot: 0x1 | 0x40000 = 0x40001.
	m, ok = parseThrottled("throttled=0x40001")
	if !ok {
		t.Fatal("parse failed")
	}
	if m["undervolt_now"] != 1 || m["throttled_since_boot"] != 1 {
		t.Fatalf("expected flags set: %v", m)
	}
	if m["throttled_now"] != 0 || m["undervolt_since_boot"] != 0 {
		t.Fatalf("unexpected flags set: %v", m)
	}
	if m["throttled_bits"] != float64(0x40001) {
		t.Fatalf("bits=%v", m["throttled_bits"])
	}
}

func TestParsePMIC(t *testing.T) {
	const s = `  VDD_CORE_A current(7)=1.09839000A
  VDD_CORE_V volt(15)=0.85555470V
     EXT5V_V volt(24)=5.02768000V
      BATT_V volt(25)=2.98375800V`
	m := parsePMIC(s)
	if !approx(m["EXT5V_V"], 5.02768) {
		t.Fatalf("EXT5V_V=%v", m["EXT5V_V"])
	}
	if !approx(m["VDD_CORE_V"], 0.8555547) {
		t.Fatalf("VDD_CORE_V=%v", m["VDD_CORE_V"])
	}
	if !approx(m["BATT_V"], 2.983758) {
		t.Fatalf("BATT_V=%v", m["BATT_V"])
	}
}

func TestCollectVcgencmd(t *testing.T) {
	vc := func(ctx context.Context, args ...string) (string, error) {
		switch args[0] {
		case "get_throttled":
			return "throttled=0x50005\n", nil
		case "pmic_read_adc":
			return "     EXT5V_V volt(24)=4.85000000V\n  VDD_CORE_V volt(15)=0.80000000V\n", nil
		}
		return "", errors.New("unknown")
	}
	vals := map[string]float64{}
	collectVcgencmd(context.Background(), vals, vc)
	if !approx(vals["supply_v"], 4.85) {
		t.Fatalf("supply_v=%v", vals["supply_v"])
	}
	// 0x50005 = undervolt_now|throttled_now|undervolt_since_boot|throttled_since_boot.
	for _, k := range []string{"undervolt_now", "throttled_now", "undervolt_since_boot", "throttled_since_boot"} {
		if vals[k] != 1 {
			t.Fatalf("%s not set: %v", k, vals)
		}
	}
}

func TestCollectVcgencmdNilRunner(t *testing.T) {
	vals := map[string]float64{}
	collectVcgencmd(context.Background(), vals, nil) // must not panic
	if len(vals) != 0 {
		t.Fatalf("expected no values, got %v", vals)
	}
}
