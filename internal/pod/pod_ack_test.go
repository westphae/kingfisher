package pod

import (
	"testing"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
)

func TestAckFailureRevertsRate(t *testing.T) {
	c := New("", nil, live.NewHub(), nil, nil, sensors.NewRegistry(), nil)
	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			{ID: wire.SensorMag, MinHz: 1, MaxHz: 200, DefaultHz: 50},
		}},
	})

	_ = c.reader.SetChannelAttr(ChMagX, "sampling_frequency", "100")
	out := <-c.cmdOut
	seq := c.cmdSeq.Add(1)
	c.trackPending(seq, wire.SensorMag, out.PrevHz)

	c.dispatch(wire.Ack{ForSeq: seq, OK: false}, "test")

	c.reader.mu.RLock()
	hz := c.reader.rates[wire.SensorMag]
	c.reader.mu.RUnlock()
	if hz != out.PrevHz {
		t.Fatalf("rate after failed ack: got %d want %d", hz, out.PrevHz)
	}
}
