package gps

import (
	"context"
	"fmt"
	"math"
	"sort"
	"sync"
	"time"

	gpsd "github.com/stratoberry/go-gpsd"
)

const (
	clockFreshWindow    = 3 * time.Second
	clockSkewAligned    = 250 * time.Millisecond
	clockGrossOffset    = 2 * time.Second
	startupProbeTimeout = 3 * time.Second
	offsetHistLen       = 32
	offsetBaselineMin   = 8
)

const (
	ClockStateWaiting = "waiting_for_fix"
	ClockStateStale   = "stale_fix"
	ClockStateOffset  = "offset_high"
	ClockStateAligned = "aligned"
)

// StartupClockCheck records what the Pi wall clock looked like just before the
// flight DB was opened. It is an assessment, not a hard gate.
type StartupClockCheck struct {
	CheckedAt   time.Time
	HasFix      bool
	Disciplined bool
	Fallback    bool
	State       string
	Reason      string
	Offset      time.Duration
}

func (s StartupClockCheck) Summary() string {
	if s.Reason == "" {
		return s.State
	}
	if s.HasFix {
		return fmt.Sprintf("%s (%s, offset=%s)", s.State, s.Reason, roundMillis(s.Offset))
	}
	return fmt.Sprintf("%s (%s)", s.State, s.Reason)
}

// ClockStatus is the live Pi-vs-GPS clock-health view exposed to the web UI.
type ClockStatus struct {
	State        string
	HasFix       bool
	Fresh        bool
	Disciplined  bool
	FixTime      time.Time
	FixAge       time.Duration
	Offset        time.Duration // recv wall minus fix epoch (includes receiver/pipeline lag)
	Baseline      time.Duration // median fix-epoch lag once enough samples exist
	Skew          time.Duration // Offset - Baseline; true wall-clock error indicator
	BaselineReady bool
	StartupCheck  StartupClockCheck
}

type offsetTracker struct {
	mu   sync.Mutex
	hist []time.Duration
}

func (t *offsetTracker) add(d time.Duration) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if len(t.hist) >= offsetHistLen {
		copy(t.hist, t.hist[1:])
		t.hist[len(t.hist)-1] = d
		return
	}
	t.hist = append(t.hist, d)
}

func (t *offsetTracker) baselineAndSkew(latest time.Duration) (baseline, skew time.Duration, ok bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if len(t.hist) < offsetBaselineMin {
		return 0, 0, false
	}
	cp := append([]time.Duration(nil), t.hist...)
	baseline = medianDuration(cp)
	return baseline, latest - baseline, true
}

func medianDuration(ds []time.Duration) time.Duration {
	if len(ds) == 0 {
		return 0
	}
	sort.Slice(ds, func(i, j int) bool { return ds[i] < ds[j] })
	return ds[len(ds)/2]
}

// ProbeStartupClock waits briefly for a TPV fix from gpsd and compares the
// current Pi wall clock to the GPS fix epoch. Kingfisher uses this before DB
// creation so the operator can tell whether startup fell back to unsynchronized
// wall time.
func ProbeStartupClock(ctx context.Context, addr string) StartupClockCheck {
	started := time.Now()
	out := StartupClockCheck{
		CheckedAt: started,
		Fallback:  true,
		State:     ClockStateWaiting,
		Reason:    "no GPS fix before DB open",
	}

	if addr == "" {
		out.Reason = "gpsd disabled"
		return out
	}

	probeCtx, cancel := context.WithTimeout(ctx, startupProbeTimeout)
	defer cancel()

	s, err := gpsd.Dial(addr)
	if err != nil {
		out.Reason = fmt.Sprintf("gpsd dial failed: %v", err)
		return out
	}
	defer s.Close()

	type probeResult struct {
		fixTime time.Time
		recv    time.Time
	}
	got := make(chan probeResult, 1)
	s.AddFilter("TPV", func(r any) {
		rep, ok := r.(*gpsd.TPVReport)
		if !ok || rep.Mode < gpsd.Mode2D || rep.Time.IsZero() {
			return
		}
		select {
		case got <- probeResult{fixTime: rep.Time, recv: time.Now()}:
		default:
		}
	})
	done := s.Watch()

	select {
	case pr := <-got:
		out.HasFix = true
		out.Offset = pr.recv.Sub(pr.fixTime)
		st := classifyClock(true, pr.fixTime, pr.recv, out.Offset, 0, 0, false)
		out.State = st.State
		out.Disciplined = st.Disciplined
		out.Fallback = !st.Disciplined
		if out.Disciplined {
			out.Reason = "fresh GPS fix at startup"
		} else if absDuration(out.Offset) > clockGrossOffset {
			out.Reason = fmt.Sprintf("Pi-vs-GPS offset exceeds %s", roundMillis(clockGrossOffset))
		} else {
			out.Reason = "GPS fix epoch lag at startup (chrony may still be slewing)"
		}
		return out
	case <-done:
		out.Reason = "gpsd watch ended before startup assessment completed"
		return out
	case <-probeCtx.Done():
		out.Reason = "no GPS fix before DB open"
		return out
	}
}

func classifyClock(hasFix bool, fixTime, recvWall time.Time, offset, baseline, skew time.Duration, baselineReady bool) ClockStatus {
	out := ClockStatus{
		State:         ClockStateWaiting,
		FixTime:       fixTime,
		Offset:        offset,
		Baseline:      baseline,
		Skew:          skew,
		BaselineReady: baselineReady,
	}
	if !hasFix || fixTime.IsZero() || recvWall.IsZero() {
		return out
	}
	out.HasFix = true
	out.FixAge = time.Since(fixTime)
	out.Fresh = out.FixAge <= clockFreshWindow
	switch {
	case !out.Fresh:
		out.State = ClockStateStale
	case baselineReady && absDuration(skew) > clockSkewAligned:
		out.State = ClockStateOffset
	case !baselineReady && absDuration(offset) > clockGrossOffset:
		out.State = ClockStateOffset
	default:
		out.State = ClockStateAligned
		out.Disciplined = true
	}
	return out
}

func absDuration(d time.Duration) time.Duration {
	if d < 0 {
		return -d
	}
	return d
}

func roundMillis(d time.Duration) time.Duration {
	ms := float64(d) / float64(time.Millisecond)
	if math.IsNaN(ms) || math.IsInf(ms, 0) {
		return 0
	}
	return time.Duration(math.Round(ms)) * time.Millisecond
}
