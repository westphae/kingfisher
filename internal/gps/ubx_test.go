package gps

import "testing"

func TestFeedUBXNavPVTNumSV(t *testing.T) {
	payload := make([]byte, 92)
	payload[ubxNavPVTNumSV] = 14
	frame := []byte{ubxSync1, ubxSync2, ubxClassNAV, ubxIDNAVPVT, 92, 0}
	frame = append(frame, payload...)
	frame = append(frame, 0, 0) // checksum ignored

	var got int
	n := feedUBX(append([]byte{0xFF, 0x00}, frame...), func(nsv int) { got = nsv })
	if got != 14 {
		t.Fatalf("numSV: got %d, want 14", got)
	}
	if n != len(frame)+2 {
		t.Fatalf("consumed %d, want %d", n, len(frame)+2)
	}
}

func TestFeedUBXPartialFrame(t *testing.T) {
	frame := []byte{ubxSync1, ubxSync2, ubxClassNAV, ubxIDNAVPVT, 92, 0, 9}
	if feedUBX(frame, func(int) { t.Fatal("unexpected callback") }) != 0 {
		t.Fatal("partial frame should not consume")
	}
}
