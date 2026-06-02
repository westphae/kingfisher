package pod

import (
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod/wire"
)

func TestLinkStats_seqGapIncrementsDropped(t *testing.T) {
	c := &Client{transport: &UDP{}}
	c.noteRx()
	c.onBatch(wire.SampleBatch{Seq: 1, Samples: nil})
	c.onBatch(wire.SampleBatch{Seq: 5, Samples: nil})

	st := c.LinkStats()
	if st.RxPackets != 2 {
		t.Fatalf("rx_packets=%d want 2", st.RxPackets)
	}
	if st.RxDropped != 3 {
		t.Fatalf("rx_dropped=%d want 3 (seq 2,3,4)", st.RxDropped)
	}
}

func TestLinkStats_connectedWhenRecentRx(t *testing.T) {
	c := &Client{transport: &UDP{}}
	c.noteRx()
	if !c.LinkStats().Connected {
		t.Fatal("expected connected after noteRx")
	}
	c.lastRxNs.Store(time.Now().Add(-10 * time.Second).UnixNano())
	if c.LinkStats().Connected {
		t.Fatal("expected disconnected after stale rx")
	}
}

func TestStatusPublishesBatteryWhenTelemetryStale(t *testing.T) {
	hub := live.NewHub()
	c := &Client{transport: &UDP{}, hub: hub, reader: newReader(make(chan outboundCmd, 1))}
	c.reader.applyReading(wire.BatteryReading{
		VoltageV: 3.60, CurrentA: 0.34, PowerW: 1.2,
	}, false)
	c.lastBatteryTelemetryNs.Store(time.Now().Add(-30 * time.Second).UnixNano())

	c.noteStatus(wire.Status{BatteryV: 3.54})

	snap := hub.SnapshotNow()
	sm, ok := snap.Devices[BatteryDeviceName]
	if !ok {
		t.Fatal("hub missing bq27441")
	}
	if sm.Values[ChBatteryV] < 3.53 || sm.Values[ChBatteryV] > 3.55 {
		t.Fatalf("battery_voltage_v=%v want ~3.54", sm.Values[ChBatteryV])
	}
	if sm.Values[ChBatteryI] < 0.33 || sm.Values[ChBatteryI] > 0.35 {
		t.Fatalf("current not preserved from cache: %v", sm.Values[ChBatteryI])
	}
}
