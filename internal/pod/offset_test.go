package pod

import (
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod/wire"
)

// TestOnBatchOffsetInitAndEMA exercises offsetNs init on the first batch
// and EMA smoothing on subsequent batches. Because recvNs = time.Now()
// keeps advancing during the test, we feed PodUptimeUs that tracks wall
// time with a known constant offset; the EMA should settle near that
// offset rather than drifting unboundedly.
func TestOnBatchOffsetInitAndEMA(t *testing.T) {
	hub := live.NewHub()
	c := New("", nil, hub, nil, nil, nil, nil)
	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			helloCap(wire.SensorStatic, 1, 50, 10),
		}},
	})

	// Synthesised offset: pretend the pod's uptime clock trails wall time
	// by 7 ms when each batch arrives.
	const podBehindMs = 7
	t0 := time.Now().UnixNano()
	for i := 0; i < 32; i++ {
		recv := time.Now().UnixNano()
		uptimeUs := uint64((recv - t0 - int64(podBehindMs*time.Millisecond)) / 1000)
		c.onBatch(wire.SampleBatch{
			PodUptimeUs: uptimeUs,
			Seq:         uint32(i + 1),
			Samples: []wire.Reading{
				wire.StaticReading{PPa: 98_000, TempC: 17, AgeUs: 0},
			},
		})
		time.Sleep(1 * time.Millisecond)
	}
	if !c.offsetInited.Load() {
		t.Fatal("offsetInited should be true after first batch")
	}
	// The offset stored is recvNs - (uptimeUs*1000). uptimeUs was set so
	// that recvNs - uptimeNs ≈ t0 + podBehindMs. We assert it sits in a
	// narrow band around that.
	want := t0 + int64(podBehindMs)*int64(time.Millisecond)
	got := c.offsetNs.Load()
	tol := int64(5 * time.Millisecond)
	if got < want-tol || got > want+tol {
		t.Fatalf("EMA offset %d ns not within %d ns of expected %d ns", got, tol, want)
	}
}

// TestOnBatchClampsOutOfWindowTs asserts that a Reading whose
// reconstructed TsNs would land outside [recvNs-10s, recvNs+1s] is
// fallback-stamped to recvNs and the tsClamped counter is bumped.
// This guards against EMA cold-start, bogus pod uptime, or anomalous
// AgeMicros emitting "year 2262" rows from underflow.
func TestOnBatchClampsOutOfWindowTs(t *testing.T) {
	hub := live.NewHub()
	c := New("", nil, hub, nil, nil, nil, nil)
	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			helloCap(wire.SensorStatic, 1, 50, 10),
		}},
	})

	before := c.tsClamped.Load()
	// AgeUs beyond the 10-min corruption ceiling (a wedged buffer or bogus
	// age) — well past any legitimate burst-store backlog, so it must be
	// clamped to recvNs rather than written as a stale/garbage timestamp.
	const huge = uint32(660_000_000) // 11 min
	tBefore := time.Now().UnixNano()
	c.onBatch(wire.SampleBatch{
		PodUptimeUs: 700_000_000, // 700 s uptime, no underflow
		Seq:         1,
		Samples: []wire.Reading{
			wire.StaticReading{PPa: 98_000, TempC: 17, AgeUs: huge},
		},
	})
	tAfter := time.Now().UnixNano()
	if got := c.tsClamped.Load(); got != before+1 {
		t.Fatalf("tsClamped=%d want %d", got, before+1)
	}
	sm, ok := hub.SnapshotNow().Devices["bmp581"]
	if !ok {
		t.Fatal("hub missing bmp581 after onBatch")
	}
	if sm.TsNs < tBefore || sm.TsNs > tAfter {
		t.Fatalf("published TsNs %d not in [%d, %d] (should fall back to recvNs)", sm.TsNs, tBefore, tAfter)
	}
}

// TestOnBatchPreservesDeepBufferTs guards against the clamp window being too
// narrow: a reading drained from a full pod ring after a link outage can be
// ~13 s old (128 samples / 10 Hz). Its reconstructed TsNs is LEGITIMATE and
// must be preserved, not collapsed onto recvNs. Regression guard for the
// tsClampPast widening.
func TestOnBatchPreservesDeepBufferTs(t *testing.T) {
	hub := live.NewHub()
	c := New("", nil, hub, nil, nil, nil, nil)
	c.reader.applyHello(wire.Hello{
		FwVersion:    1,
		ProtoVersion: wire.ProtoVersion,
		Caps: wire.Capabilities{Sensors: []wire.SensorCap{
			helloCap(wire.SensorStatic, 1, 50, 10),
		}},
	})

	// PodUptimeUs large enough that a 12 s age does not underflow. The
	// offset on the first batch is exact (recvNs - podUptimeNs), so the
	// 12 s-old reading reconstructs to recvNs - 12 s, comfortably inside
	// the 5-min past window.
	const ageUs = uint32(12_000_000) // 12 s
	tBefore := time.Now().UnixNano()
	c.onBatch(wire.SampleBatch{
		PodUptimeUs: 100_000_000, // 100 s uptime, no underflow
		Seq:         1,
		Samples: []wire.Reading{
			wire.StaticReading{PPa: 98_000, TempC: 17, AgeUs: ageUs},
		},
	})
	if got := c.tsClamped.Load(); got != 0 {
		t.Fatalf("tsClamped=%d, want 0 (12 s-old reading is legitimate)", got)
	}
	sm, ok := hub.SnapshotNow().Devices["bmp581"]
	if !ok {
		t.Fatal("hub missing bmp581 after onBatch")
	}
	// Should sit ~12 s before receive, NOT at recvNs.
	ageNs := tBefore - sm.TsNs
	if ageNs < 11*int64(time.Second) || ageNs > 13*int64(time.Second) {
		t.Fatalf("reconstructed age %d ns not ~12 s — deep-buffer TsNs was clamped/lost", ageNs)
	}
}
