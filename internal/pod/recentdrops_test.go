package pod

import (
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/pod/wire"
)

func statusWithDrops(n uint32) wire.Status {
	return wire.Status{DroppedReadings: n}
}

// The pod's cumulative overrun counter must not warn on first sight (backlog
// from before the hub listened), only on growth while connected.
func TestRecentDropsBaseline(t *testing.T) {
	c := &Client{}

	// First status after connect carries a big backlog: baseline, no warn.
	c.noteStatus(statusWithDrops(5000))
	if c.lastDropNs.Load() != 0 {
		t.Fatalf("baseline status marked a recent drop")
	}

	// Growth while connected: warn.
	c.noteStatus(statusWithDrops(5010))
	if c.lastDropNs.Load() == 0 {
		t.Fatalf("overrun growth did not mark a recent drop")
	}

	// Pod reboot (counter reset): re-baseline, no new warn.
	c.lastDropNs.Store(0)
	c.noteStatus(statusWithDrops(3))
	if c.lastDropNs.Load() != 0 {
		t.Fatalf("counter reset marked a recent drop")
	}
}

// A dead link period must re-baseline: whatever the pod lost while we were
// away was not receivable.
func TestRecentDropsRebaselineAfterGap(t *testing.T) {
	c := &Client{}
	c.noteStatus(statusWithDrops(100))
	c.noteRx()

	// Simulate a stale gap by backdating lastRxNs beyond linkStaleTimeout.
	c.lastRxNs.Store(time.Now().Add(-2 * linkStaleTimeout).UnixNano())
	c.noteRx() // reconnect → unbaselined

	c.noteStatus(statusWithDrops(900)) // backlog from the outage: baseline only
	if c.lastDropNs.Load() != 0 {
		t.Fatalf("post-outage backlog marked a recent drop")
	}
	c.noteStatus(statusWithDrops(901)) // now genuine growth
	if c.lastDropNs.Load() == 0 {
		t.Fatalf("post-rebaseline growth did not mark a recent drop")
	}
}

func TestRecentDropsWindow(t *testing.T) {
	c := &Client{}
	c.lastDropNs.Store(time.Now().Add(-30 * time.Second).UnixNano())
	if st := c.LinkStats(); !st.RecentDrops {
		t.Errorf("drop 30s ago should be recent")
	}
	c.lastDropNs.Store(time.Now().Add(-2 * time.Minute).UnixNano())
	if st := c.LinkStats(); st.RecentDrops {
		t.Errorf("drop 2min ago should not be recent")
	}
}
