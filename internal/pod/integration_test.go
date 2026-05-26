package pod

import (
	"net"
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
)

// TestUDPIntegration runs a real UDP socket end-to-end: a fake pod
// encodes a SampleBatch and sends it; the Client's Recv loop decodes it
// and publishes a live.Sample.
func TestUDPIntegration(t *testing.T) {
	transport, err := ListenUDP("127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	listenAddr := transport.conn.LocalAddr().(*net.UDPAddr)

	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New(listenAddr.String(), transport, hub, nil, nil, reg, nil)

	stop := make(chan struct{})
	done := make(chan struct{})
	go func() {
		c.Run(stop)
		close(done)
	}()
	defer func() {
		close(stop)
		<-done
	}()

	// Send a Hello so the reader knows the caps and we can later assert
	// the registry is populated.
	send := func(t *testing.T, f wire.Frame) {
		t.Helper()
		conn, err := net.DialUDP("udp", nil, listenAddr)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Close()
		buf := make([]byte, 512)
		n, err := wire.Encode(f, buf)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := conn.Write(buf[:n]); err != nil {
			t.Fatal(err)
		}
	}

	send(t, wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorAirspeed, MinHz: 1, MaxHz: 50, DefaultHz: 10},
			{ID: wire.SensorStatic, MinHz: 1, MaxHz: 50, DefaultHz: 10},
			{ID: wire.SensorMag, MinHz: 1, MaxHz: 200, DefaultHz: 50},
		}},
	})

	send(t, wire.SampleBatch{
		PodUptimeUs: 5_000_000,
		Seq:         1,
		Samples: []wire.Reading{
			wire.AirspeedReading{DpPa: 250.0, TempC: 17.0, AgeUs: 0},
			wire.StaticReading{PPa: 97_500.0, TempC: 17.0, AgeUs: 0},
			wire.MagReading{XUt: 12.0, YUt: 8.0, ZUt: 40.0, AgeUs: 0},
		},
	})

	// Poll the hub for up to 1 s — the recv goroutine is async.
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		snap := hub.SnapshotNow()
		air, okAir := snap.Devices["ms4525"]
		mag, okMag := snap.Devices["mmc5983"]
		if okAir && okMag && air.Values[ChAirspeedDP] == 250.0 && mag.Values[ChMagX] == 12.0 {
			for _, dev := range []string{"bmp581", "mmc5983", "ms4525"} {
				views := reg.Get(dev)
				if len(views) != 1 || views[0].Attr != "sampling_frequency" || !views[0].Writable {
					t.Errorf("%s registry views: %+v", dev, views)
				}
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("hub never received the pod sample")
}
