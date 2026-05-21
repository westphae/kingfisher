package pod

import (
	"testing"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
)

func TestApplyDeviceConfigFromJSONAttrs(t *testing.T) {
	cmdOut := make(chan outboundCmd, 4)
	r := newReader(cmdOut)
	r.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorMag, MinHz: 1, MaxHz: 200, DefaultHz: 10},
			{ID: wire.SensorStatic, MinHz: 1, MaxHz: 50, DefaultHz: 10},
		}},
	})

	dev := config.Device{
		Attrs: map[string]string{
			sensors.JoinIIOAttr("mag", "sampling_frequency"):    "50",
			sensors.JoinIIOAttr("static", "sampling_frequency"): "25",
		},
	}
	outs := r.ApplyDeviceConfig(dev)
	if len(outs) != 2 {
		t.Fatalf("got %d cmds, want 2", len(outs))
	}
	r.mu.RLock()
	if r.rates[wire.SensorMag] != 50 {
		t.Errorf("mag rate: got %d want 50", r.rates[wire.SensorMag])
	}
	if r.rates[wire.SensorStatic] != 25 {
		t.Errorf("static rate: got %d want 25", r.rates[wire.SensorStatic])
	}
	r.mu.RUnlock()

	// Hello defaults must not clobber configured rates.
	r.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorMag, MinHz: 1, MaxHz: 200, DefaultHz: 10},
		}},
	})
	r.mu.RLock()
	if r.rates[wire.SensorMag] != 50 {
		t.Errorf("after hello mag rate: got %d want 50", r.rates[wire.SensorMag])
	}
	r.mu.RUnlock()
}
