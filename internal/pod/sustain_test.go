package pod

import "testing"

func TestSustainableRates(t *testing.T) {
	if !SustainableRates(50, 0, 0) {
		t.Fatal("50 Hz static alone should be sustainable (FIFO model)")
	}
	if !SustainableRates(25, 50, 0) {
		t.Fatal("25+50 static+mag should be sustainable")
	}
	if !SustainableRates(50, 50, 0) {
		t.Fatal("50+50 static+mag should be sustainable with FIFO drain model")
	}
}
