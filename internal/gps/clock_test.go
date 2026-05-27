package gps

import (
	"testing"
	"time"
)

func TestClassifyClockAlignedSmallSkew(t *testing.T) {
	now := time.Now()
	st := classifyClock(true, now.Add(-100*time.Millisecond), now, 680*time.Millisecond, 675*time.Millisecond, 5*time.Millisecond, true)
	if st.State != ClockStateAligned {
		t.Fatalf("state=%q want %q", st.State, ClockStateAligned)
	}
	if !st.Disciplined || !st.Fresh || !st.HasFix {
		t.Fatalf("aligned clock should be disciplined and fresh: %+v", st)
	}
}

func TestClassifyClockOffsetHighSkew(t *testing.T) {
	now := time.Now()
	st := classifyClock(true, now.Add(-100*time.Millisecond), now, 680*time.Millisecond, 675*time.Millisecond, 500*time.Millisecond, true)
	if st.State != ClockStateOffset {
		t.Fatalf("state=%q want %q", st.State, ClockStateOffset)
	}
	if st.Disciplined {
		t.Fatalf("offset-high clock should not be disciplined: %+v", st)
	}
}

func TestClassifyClockWarmingUp(t *testing.T) {
	now := time.Now()
	st := classifyClock(true, now.Add(-100*time.Millisecond), now, 680*time.Millisecond, 0, 0, false)
	if st.State != ClockStateAligned {
		t.Fatalf("state=%q want %q during warmup", st.State, ClockStateAligned)
	}
	if !st.Disciplined {
		t.Fatalf("expected disciplined during warmup with typical lag: %+v", st)
	}
}

func TestClassifyClockStale(t *testing.T) {
	now := time.Now()
	st := classifyClock(true, now.Add(-4*time.Second), now, 10*time.Millisecond, 10*time.Millisecond, 0, true)
	if st.State != ClockStateStale {
		t.Fatalf("state=%q want %q", st.State, ClockStateStale)
	}
	if st.Fresh {
		t.Fatalf("stale clock should not be fresh: %+v", st)
	}
}

func TestClassifyClockWaitingForFix(t *testing.T) {
	st := classifyClock(false, time.Time{}, time.Time{}, 0, 0, 0, false)
	if st.State != ClockStateWaiting {
		t.Fatalf("state=%q want %q", st.State, ClockStateWaiting)
	}
	if st.HasFix {
		t.Fatalf("waiting clock should not report a fix: %+v", st)
	}
}

func TestMedianDuration(t *testing.T) {
	got := medianDuration([]time.Duration{100 * time.Millisecond, 500 * time.Millisecond, 200 * time.Millisecond})
	if got != 200*time.Millisecond {
		t.Fatalf("median=%v want 200ms", got)
	}
}
