package calibrate

import (
	"math"
	"testing"
)

func TestDetectFace(t *testing.T) {
	cases := []struct {
		a    [3]float64
		want Face
	}{
		{[3]float64{G0, 0.2, -0.1}, FacePlusX},
		{[3]float64{-G0, 0.1, 0}, FaceMinusX},
		{[3]float64{0.1, G0, -0.2}, FacePlusY},
		{[3]float64{0, -G0, 0.1}, FaceMinusY},
		{[3]float64{0.2, -0.1, G0}, FacePlusZ},
		{[3]float64{0.1, 0.1, -G0}, FaceMinusZ},
	}
	for _, tc := range cases {
		got, dom, ok := DetectFace(tc.a)
		if !ok || got != tc.want {
			t.Fatalf("DetectFace(%v)=%v ok=%v want %v (dom=%v)", tc.a, got, ok, tc.want, dom)
		}
		if dom < faceDominanceMin {
			t.Fatalf("dom %v", dom)
		}
	}
	// Diagonal / tumbling — no clear face.
	_, _, ok := DetectFace([3]float64{5, 5, 5})
	if ok {
		t.Fatal("expected no detect on diagonal")
	}
	_, _, ok = DetectFace([3]float64{1, 0, 0})
	if ok {
		t.Fatal("expected no detect on tiny vector")
	}
	_ = math.NaN
}
