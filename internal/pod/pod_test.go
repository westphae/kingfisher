package pod

import (
	"bytes"
	"log"
	"strings"
	"testing"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/location"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
)

func helloCap(id wire.SensorID, min, max, def uint16) wire.SensorCap {
	return wire.SensorCap{
		ID: id, MinHz: min, MaxHz: max, DefaultHz: def,
		DeviceName: wire.NewDeviceName(DefaultDeviceName(id)),
	}
}

func TestOnBatchPublishesSplitDevices(t *testing.T) {
	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New("", nil, hub, nil, nil, reg, nil)

	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			helloCap(wire.SensorAirspeed, 1, 50, 10),
			helloCap(wire.SensorStatic, 1, 50, 10),
			helloCap(wire.SensorMag, 1, 200, 50),
		}},
	})

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
	static, ok := snap.Devices["bmp581"]
	if !ok {
		t.Fatal("hub missing bmp581")
	}
	if static.Values[ChStaticP] != 98_000.0 || static.Values[ChStaticTemp] != 17.5 {
		t.Errorf("bmp581: %v", static.Values)
	}
	if _, ok := static.Values[ChMagX]; ok {
		t.Errorf("bmp581 must not contain mag channels")
	}
	mag, ok := snap.Devices["mmc5983"]
	if !ok || mag.Values[ChMagX] != 22.0 {
		t.Errorf("mmc5983: %v", mag.Values)
	}
	air, ok := snap.Devices["ms4525"]
	if !ok || air.Values[ChAirspeedDP] != 100.0 {
		t.Errorf("ms4525: %v", air.Values)
	}
}

func TestOnBatchMultipleStaticTimestamps(t *testing.T) {
	hub := live.NewHub()
	c := New("", nil, hub, nil, nil, nil, nil)
	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			helloCap(wire.SensorStatic, 1, 50, 10),
		}},
	})

	c.onBatch(wire.SampleBatch{
		PodUptimeUs: 1_100_000,
		Seq:         1,
		Samples: []wire.Reading{
			wire.StaticReading{PPa: 98_000.0, TempC: 17.0, AgeUs: 100_000},
			wire.StaticReading{PPa: 98_100.0, TempC: 17.1, AgeUs: 0},
		},
	})
	snap := hub.SnapshotNow()
	last := snap.Devices["bmp581"]
	if last.Values[ChStaticP] != 98_100.0 {
		t.Fatalf("hub keeps latest static: %v", last.Values)
	}
}

func TestSetSamplingFrequencyEnqueuesCmd(t *testing.T) {
	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New("", nil, hub, nil, nil, reg, nil)

	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			helloCap(wire.SensorMag, 1, 200, 50),
		}},
	})

	if err := c.reader.SetChannelAttr("mmc5983", "sampling_frequency", "100"); err != nil {
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

	if err := c.reader.SetChannelAttr("mmc5983", "sampling_frequency", "9999"); err == nil {
		t.Fatal("expected out-of-range error")
	}
	if err := c.reader.SetChannelAttr(ChMagY, "sampling_frequency", "100"); err == nil {
		t.Fatal("expected non-settings channel error")
	}
}

func TestSetDesignCapacityEnqueuesCmd(t *testing.T) {
	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New("", nil, hub, nil, nil, reg, nil)

	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			helloCap(wire.SensorBattery, 1, 2, 1),
		}},
	})

	if err := c.reader.SetChannelAttr(BatteryDeviceName, AttrDesignCapacityMah, "900"); err != nil {
		t.Fatalf("SetChannelAttr: %v", err)
	}
	select {
	case out := <-c.cmdOut:
		set, ok := out.Cmd.(wire.CmdSetAttr)
		if !ok {
			t.Fatalf("got %T, want CmdSetAttr", out.Cmd)
		}
		if set.Sensor != wire.SensorBattery || set.Key != wire.AttrDesignCapacity || set.Value != 900 {
			t.Errorf("got %+v", set)
		}
	default:
		t.Fatal("no Cmd enqueued")
	}

	if err := c.reader.SetChannelAttr(BatteryDeviceName, AttrDesignCapacityMah, "50"); err == nil {
		t.Fatal("expected out-of-range error")
	}
}

func TestHelloProtoVersionMismatchWarns(t *testing.T) {
	var buf bytes.Buffer
	old := log.Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(old)

	hub := live.NewHub()
	c := New("", nil, hub, nil, nil, nil, nil)
	mismatch := wire.Hello{FwVersion: 1, ProtoVersion: wire.ProtoVersion + 1}

	c.dispatch(mismatch, "test")
	if !strings.Contains(buf.String(), "proto mismatch") {
		t.Fatalf("expected proto mismatch warning, got: %q", buf.String())
	}

	// Same mismatched version again -> deduped, no second warning.
	buf.Reset()
	c.dispatch(mismatch, "test")
	if strings.Contains(buf.String(), "proto mismatch") {
		t.Errorf("duplicate proto mismatch warning not suppressed: %q", buf.String())
	}

	// Matching version -> no warning.
	buf.Reset()
	c.dispatch(wire.Hello{FwVersion: 1, ProtoVersion: wire.ProtoVersion}, "test")
	if strings.Contains(buf.String(), "proto mismatch") {
		t.Errorf("unexpected warning for matching proto: %q", buf.String())
	}
}

func TestHelloDoesNotPublishAggregatePodDevice(t *testing.T) {
	hub := live.NewHub()
	c := New("", nil, hub, nil, nil, nil, nil)
	c.dispatch(wire.Hello{
		FwVersion:    0x0003_0000,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			helloCap(wire.SensorStatic, 1, 50, 10),
		}},
	}, "192.168.10.94:4711")
	if _, ok := hub.SnapshotNow().Devices[DeviceName]; ok {
		t.Fatal("hub should not publish legacy aggregate pod device")
	}
}

func TestRegistrySnapshotAfterHello(t *testing.T) {
	hub := live.NewHub()
	reg := sensors.NewRegistry()
	c := New("", nil, hub, nil, nil, reg, nil)

	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			helloCap(wire.SensorAirspeed, 1, 50, 10),
			helloCap(wire.SensorStatic, 1, 50, 10),
			helloCap(wire.SensorMag, 1, 200, 50),
		}},
	})
	c.refreshRegistryViews()

	for _, device := range []string{"bmp581", "mmc5983", "ms4525"} {
		views := reg.Get(device)
		if len(views) != 1 {
			t.Fatalf("%s: got %d attr rows, want 1 (%+v)", device, len(views), views)
		}
		v := views[0]
		if v.Attr != "sampling_frequency" || !v.Writable || v.Location != location.Pod {
			t.Errorf("%s: got %+v", device, v)
		}
	}
}
