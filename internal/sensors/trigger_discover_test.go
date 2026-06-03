package sensors

import "testing"

func TestDiscoverIIOTrigger(t *testing.T) {
	if discoverIIOTrigger("") != "" {
		t.Fatal("empty name")
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
