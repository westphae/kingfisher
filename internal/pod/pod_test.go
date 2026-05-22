package pod

import (
	"testing"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
)

func TestOnBatchPublishesAllChannels(t *testing.T) {
	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New("", nil, hub, nil, reg, nil)

	// Seed caps so the reader knows the legal Hz ranges for each sensor.
	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorAirspeed, MinHz: 1, MaxHz: 50, DefaultHz: 10},
			{ID: wire.SensorStatic, MinHz: 1, MaxHz: 50, DefaultHz: 10},
			{ID: wire.SensorMag, MinHz: 1, MaxHz: 200, DefaultHz: 50},
		}},
	})

	// First batch contains one of each. Sticky cache should fill in.
	// All values chosen with exact float32 representations so equality
	// checks below are stable.
	c.onBatch(wire.SampleBatch{
		PodUptimeUs: 1_000_000,
		Seq:         1,
		Samples: []wire.Reading{
			wire.AirspeedReading{DpPa: 100.0, TempC: 17.0, AgeUs: 0},
			wire.StaticReading{PPa: 98_000.0, TempC: 17.5, AgeUs: 0},
			wire.MagReading{XUt: 22.0, YUt: -3.0, ZUt: 41.0, AgeUs: 0},
		},
	})

	snap := hub.SnapshotNow()
	sample, ok := snap.Devices[DeviceName]
	if !ok {
		t.Fatal("hub has no pod sample")
	}
	want := map[string]float64{
		ChAirspeedDP: 100.0, ChAirspeedTemp: 17.0,
		ChStaticP: 98_000.0, ChStaticTemp: 17.5,
		ChMagX: 22.0, ChMagY: -3.0, ChMagZ: 41.0,
	}
	for k, v := range want {
		if sample.Values[k] != v {
			t.Errorf("channel %s: got %v want %v", k, sample.Values[k], v)
		}
	}

	// Second batch contains only a mag reading; sticky cache must still
	// expose airspeed_dp_pa and static_pressure_pa in the published sample.
	c.onBatch(wire.SampleBatch{
		PodUptimeUs: 1_020_000,
		Seq:         2,
		Samples: []wire.Reading{
			wire.MagReading{XUt: 23.0, YUt: -3.0, ZUt: 41.5, AgeUs: 0},
		},
	})
	snap = hub.SnapshotNow()
	sample = snap.Devices[DeviceName]
	if sample.Values[ChAirspeedDP] != 100.0 {
		t.Errorf("airspeed_dp_pa lost after mag-only batch: %v", sample.Values[ChAirspeedDP])
	}
	if sample.Values[ChMagX] != 23.0 {
		t.Errorf("mag_x_ut not updated: %v", sample.Values[ChMagX])
	}
}

func TestSetSamplingFrequencyEnqueuesCmd(t *testing.T) {
	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New("", nil, hub, nil, reg, nil)

	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorMag, MinHz: 1, MaxHz: 200, DefaultHz: 50},
		}},
	})

	if err := c.reader.SetChannelAttr("mag", "sampling_frequency", "100"); err != nil {
		t.Fatalf("SetChannelAttr: %v", err)
	}
	select {
	case out := <-c.cmdOut:
		set, ok := out.Cmd.(wire.CmdSetRate)
		if !ok {
			t.Fatalf("got %T, want CmdSetRate", out.Cmd)
		}
		if set.Sensor != wire.SensorMag || set.Hz != 100 {
			t.Errorf("got %+v", set)
		}
		if !out.HasPrev {
			t.Error("expected HasPrev for rollback")
		}
	default:
		t.Fatal("no Cmd enqueued")
	}

	// Out-of-range rejected.
	if err := c.reader.SetChannelAttr("mag", "sampling_frequency", "9999"); err == nil {
		t.Fatal("expected out-of-range error")
	}
	// Per-channel rates are not supported (one rate for the whole mag sensor).
	if err := c.reader.SetChannelAttr(ChMagY, "sampling_frequency", "100"); err == nil {
		t.Fatal("expected non-settings channel error")
	}
}

func TestHelloPublishesToHub(t *testing.T) {
	hub := live.NewHub()
	c := New("", nil, hub, nil, nil, nil)
	c.dispatch(wire.Hello{
		FwVersion:    0x0003_0000,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorStatic, MinHz: 1, MaxHz: 50, DefaultHz: 10},
		}},
	}, "192.168.10.94:4711")
	if _, ok := hub.SnapshotNow().Devices[DeviceName]; !ok {
		t.Fatal("hub missing pod after Hello")
	}
}

func TestRegistrySnapshotAfterHello(t *testing.T) {
	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New("", nil, hub, nil, reg, nil)

	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorAirspeed, MinHz: 1, MaxHz: 50, DefaultHz: 10},
			{ID: wire.SensorStatic, MinHz: 1, MaxHz: 50, DefaultHz: 10},
			{ID: wire.SensorMag, MinHz: 1, MaxHz: 200, DefaultHz: 50},
		}},
	})
	c.refreshRegistryViews()

	views := reg.Get(DeviceName)
	if len(views) == 0 {
		t.Fatal("registry has no views for pod")
	}
	var writable int
	var channels []string
	for _, v := range views {
		if v.Attr == "sampling_frequency" && v.Writable {
			writable++
			channels = append(channels, v.Channel)
		}
	}
	if writable != 3 {
		t.Errorf("got %d writable sampling_frequency rows, want 3 (views=%+v)", writable, views)
	}
	for _, want := range []string{"static", "mag", "airspeed"} {
		found := false
		for _, ch := range channels {
			if ch == want {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("missing settings channel %q (got %v)", want, channels)
		}
	}
}
