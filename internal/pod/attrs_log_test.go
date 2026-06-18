package pod

import (
	"path/filepath"
	"testing"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/store"
)

func TestLogPodSensorAttrsOnHello(t *testing.T) {
	st, err := store.Open(filepath.Join(t.TempDir(), "f"), "N1")
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	c := New("", nil, live.NewHub(), nil, st, nil, nil)
	c.dispatch(wire.Hello{
		FwVersion: 0x0004_0004,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorStatic, MinHz: 1, MaxHz: 20, DefaultHz: 10, DeviceName: wire.NewDeviceName("bmp581")},
			{ID: wire.SensorMag, MinHz: 1, MaxHz: 20, DefaultHz: 10, DeviceName: wire.NewDeviceName("mmc5983")},
			{ID: wire.SensorAirspeed, MinHz: 1, MaxHz: 50, DefaultHz: 10, DeviceName: wire.NewDeviceName("ms4525")},
		},
		},
	}, "test")

	var n int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM sensor_attrs WHERE location = 'pod'`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 12 { // 3 devices × (sampling_frequency + min/max/default_hz)
		t.Fatalf("sensor_attrs rows = %d, want 12", n)
	}
}

func TestFlightLogAttrRecordsIncludeCaps(t *testing.T) {
	r := newReader(nil)
	r.applyHello(wire.Hello{
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorMag, MinHz: 2, MaxHz: 15, DefaultHz: 8, DeviceName: wire.NewDeviceName("mmc5983")},
		}},
	})
	recs := r.FlightLogAttrRecordsForUIDevice("mmc5983")
	if len(recs) != 4 {
		t.Fatalf("recs len %d, want 4", len(recs))
	}
}
