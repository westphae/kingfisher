package airports

import "testing"

func TestNearestKnownFields(t *testing.T) {
	cases := []struct {
		name     string
		lat, lon float64
		want     string
	}{
		{"McKinney National", 33.1779, -96.5905, "KTKI"},
		{"Addison", 32.9686, -96.8364, "KADS"},
		{"Dallas Love", 32.8447, -96.8477, "KDAL"},
	}
	for _, c := range cases {
		ap, d, ok := Nearest(c.lat, c.lon, 5)
		if !ok {
			t.Errorf("%s: no airport within 5 km", c.name)
			continue
		}
		if ap.Ident != c.want {
			t.Errorf("%s: got %s (%.1f km), want %s", c.name, ap.Ident, d, c.want)
		}
	}
}

func TestNearestNoneInRange(t *testing.T) {
	// Middle of the Gulf of Mexico.
	if ap, d, ok := Nearest(25.5, -90.0, 5); ok {
		t.Errorf("expected none, got %s at %.1f km", ap.Ident, d)
	}
}

func BenchmarkNearest(b *testing.B) {
	for i := 0; i < b.N; i++ {
		Nearest(33.1779, -96.5905, 5)
	}
}
