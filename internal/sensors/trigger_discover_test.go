package sensors

import "testing"

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
