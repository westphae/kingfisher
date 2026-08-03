package sensors

import (
	"testing"

	"github.com/westphae/kingfisher/internal/config"
)

func TestDiscoverIIOTrigger(t *testing.T) {
	if discoverIIOTrigger("") != "" {
		t.Fatal("empty name")
	}
}

func TestSnapSamplingFrequency(t *testing.T) {
	avail := "1.562500 3.125000 12.500000 25.000000 100.000000"
	tok, hz, err := snapSamplingFrequency(10, avail)
	if err != nil || tok != "12.500000" || hz != 12.5 {
		t.Fatalf("10 Hz: tok=%q hz=%v err=%v", tok, hz, err)
	}
	tok, hz, err = snapSamplingFrequency(100, avail)
	if err != nil || tok != "100.000000" || hz != 100 {
		t.Fatalf("100 Hz: tok=%q hz=%v err=%v", tok, hz, err)
	}
}

func TestUsesHWFIFOBuffer(t *testing.T) {
	if !usesHWFIFOBuffer("icm45686-gyro") {
		t.Fatal("gyro")
	}
	if !usesHWFIFOBuffer("icm45686-accel") {
		t.Fatal("accel")
	}
	if usesHWFIFOBuffer("icm20948") {
		t.Fatal("20948")
	}
}

func TestConfiguredChipHz(t *testing.T) {
	dev := config.Device{
		SampleHz: 25,
		Attrs:    map[string]string{"sampling_frequency": "200"},
	}
	if got := configuredChipHz(dev, 25); got != 200 {
		t.Fatalf("chip hz: got %v want 200", got)
	}
	if got := configuredChipHz(config.Device{SampleHz: 25}, 25); got != 25 {
		t.Fatalf("fallback: got %v want 25", got)
	}
}

func TestBoxcarRatio(t *testing.T) {
	if boxcarRatio(200, 25) != 8 {
		t.Fatalf("200/25: got %d", boxcarRatio(200, 25))
	}
	if boxcarRatio(25, 25) != 1 {
		t.Fatalf("equal: got %d", boxcarRatio(25, 25))
	}
	if boxcarRatio(30, 25) != 1 {
		t.Fatalf("near: got %d", boxcarRatio(30, 25))
	}
}
