package pod

import (
	"testing"
	"time"

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
