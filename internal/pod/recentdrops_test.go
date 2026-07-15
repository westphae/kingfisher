package pod

import (
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/config"
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

// Since the burst protocol, a quiet period must NOT re-baseline: anything the
// pod dropped while silent was stored-data loss and must warn on reconnect.
// Only a counter reset (pod reboot) re-baselines.
func TestRecentDropsWarnAcrossGap(t *testing.T) {
	c := &Client{}
	c.noteStatus(statusWithDrops(100))
	c.noteRx()

	// Simulate a long quiet period (e.g. burst collect or AP outage).
	c.lastRxNs.Store(time.Now().Add(-2 * linkStaleTimeout).UnixNano())
	c.noteRx()

	c.noteStatus(statusWithDrops(900)) // grew while quiet: real loss, warn
	if c.lastDropNs.Load() == 0 {
		t.Fatalf("overrun growth across a quiet period did not warn")
	}
}

// A burst-mode pod that is silent within its collect window reports
// BurstQuiet; once the allowance passes, the silence is a real problem.
func TestBurstQuietAllowance(t *testing.T) {
	c := &Client{}
	c.noteStatus(wire.Status{PowerMode: powerModeBurstCollect})
	st := c.LinkStats()
	if st.PowerMode != "burst" {
		t.Fatalf("PowerMode = %q, want burst", st.PowerMode)
	}
	if !st.BurstQuiet {
		t.Errorf("fresh burst status should be BurstQuiet")
	}

	c.lastStatusNs.Store(time.Now().Add(-time.Duration(config.DefaultPodBurstWindowS)*time.Second - 2*time.Minute).UnixNano())
	if st := c.LinkStats(); st.BurstQuiet || st.BurstLost {
		t.Errorf("silence just beyond the allowance should be overdue, not quiet/lost: quiet=%v lost=%v", st.BurstQuiet, st.BurstLost)
	}

	// Far beyond any collect window: presumed protect sleep or dead link.
	c.lastStatusNs.Store(time.Now().Add(-burstLostTimeout - time.Minute).UnixNano())
	if st := c.LinkStats(); !st.BurstLost {
		t.Errorf("silence beyond burstLostTimeout should be BurstLost")
	}

	c.noteStatus(wire.Status{PowerMode: powerModeProtect})
	if st := c.LinkStats(); !st.ProtectSleep || st.PowerMode != "protect" {
		t.Errorf("protect status: got PowerMode=%q ProtectSleep=%v", st.PowerMode, st.ProtectSleep)
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
