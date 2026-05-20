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
	c := New("", nil, hub, nil, reg)

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
	// expose airspeed_dp and static_p in the published sample.
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
		t.Errorf("airspeed_dp lost after mag-only batch: %v", sample.Values[ChAirspeedDP])
	}
	if sample.Values[ChMagX] != 23.0 {
		t.Errorf("mag_x not updated: %v", sample.Values[ChMagX])
	}
}

func TestSetSamplingFrequencyEnqueuesCmd(t *testing.T) {
	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New("", nil, hub, nil, reg)

	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorMag, MinHz: 1, MaxHz: 200, DefaultHz: 50},
		}},
	})

	if err := c.reader.SetChannelAttr(ChMagX, "sampling_frequency", "100"); err != nil {
		t.Fatalf("SetChannelAttr: %v", err)
	}
	select {
	case cmd := <-c.cmdOut:
		set, ok := cmd.(wire.CmdSetRate)
		if !ok {
			t.Fatalf("got %T, want CmdSetRate", cmd)
		}
		if set.Sensor != wire.SensorMag || set.Hz != 100 {
			t.Errorf("got %+v", set)
		}
	default:
		t.Fatal("no Cmd enqueued")
	}

	// Out-of-range rejected.
	if err := c.reader.SetChannelAttr(ChMagX, "sampling_frequency", "9999"); err == nil {
		t.Fatal("expected out-of-range error")
	}
	// Non-primary channel rejected.
	if err := c.reader.SetChannelAttr(ChMagY, "sampling_frequency", "100"); err == nil {
		t.Fatal("expected non-primary channel error")
	}
}

func TestRegistrySnapshotAfterHello(t *testing.T) {
	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New("", nil, hub, nil, reg)

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
	// Expect exactly three writable sampling_frequency rows (one per sensor).
	var writable int
	for _, v := range views {
		if v.Attr == "sampling_frequency" && v.Writable {
			writable++
		}
	}
	if writable != 3 {
		t.Errorf("got %d writable sampling_frequency rows, want 3 (views=%+v)", writable, views)
	}
}
