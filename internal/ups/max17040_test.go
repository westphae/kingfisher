package ups

import (
	"math"
	"testing"
)

// Golden values from the MAX17040 datasheet formulas, plus a live reading
// taken from this X1200 on 2026-07-17 (i2cget words are byte-swapped; the
// raw values here are wire/MSB-first order as readWord assembles them).
func TestVCellVolts(t *testing.T) {
	cases := []struct {
		raw  uint16
		want float64
	}{
		{0x0000, 0},
		{0xC7F0, 3.99875},
		{0xD110, 4.18125}, // live reading, charging near full
	}
	for _, c := range cases {
		if got := vcellVolts(c.raw); math.Abs(got-c.want) > 1e-9 {
			t.Errorf("vcellVolts(0x%04X) = %v, want %v", c.raw, got, c.want)
		}
	}
}

func TestSocPct(t *testing.T) {
	cases := []struct {
		raw  uint16
		want float64
	}{
		{0x0000, 0},
		{0x6280, 98.5},
		{0x0A00, 10},
		{0x6273, 98.44921875}, // live reading
	}
	for _, c := range cases {
		if got := socPct(c.raw); math.Abs(got-c.want) > 1e-9 {
			t.Errorf("socPct(0x%04X) = %v, want %v", c.raw, got, c.want)
		}
	}
}

// The chip sends MSB first on a raw I2C read; the little-endian swap seen
// with SMBus word reads must NOT be applied here.
func TestWordFromBytesMSBFirst(t *testing.T) {
	if got := wordFromBytes(0xD1, 0x10); got != 0xD110 {
		t.Errorf("wordFromBytes(0xD1, 0x10) = 0x%04X, want 0xD110", got)
	}
}
