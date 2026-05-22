package pod

import "testing"

func TestSustainableRates(t *testing.T) {
	if !SustainableRates(25, 50, 0) {
		t.Fatal("25+50 should be sustainable")
	}
	if SustainableRates(50, 50, 0) {
		t.Fatal("50+50 static+mag should be rejected")
	}
	if SustainableRates(50, 50, 50) {
		t.Fatal("50+50+50 should be rejected")
	}
}
