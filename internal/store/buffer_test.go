package store

import (
	"errors"
	"testing"

	"github.com/westphae/kingfisher/internal/live"
)

// fakeStore is a flushTarget that can be made to fail FlushBatch on demand,
// so the buffer's failure paths (re-queue, cap, degraded) are testable
// without a real on-disk SQLite error.
type fakeStore struct {
	failFlush bool
	flushed   map[string]int
}

func (f *fakeStore) EnsureTable(string, []string) error { return nil }
func (f *fakeStore) FlushBatch(device string, _ []string, s []live.Sample) error {
	if f.failFlush {
		return errors.New("disk full")
	}
	if f.flushed == nil {
		f.flushed = map[string]int{}
	}
	f.flushed[device] += len(s)
	return nil
}
func (f *fakeStore) CheckpointWAL() error { return nil }

func newTestBuffer(s flushTarget) *Buffer {
	return &Buffer{
		store:   s,
		pending: map[string][]live.Sample{},
		cols:    map[string]map[string]bool{},
		colsOrd: map[string][]string{},
	}
}

func sample(ts int64) live.Sample {
	return live.Sample{Device: "d", TsNs: ts, Values: map[string]float64{"a": float64(ts)}}
}

// A failed flush must NOT discard the rows it was about to write; they must
// remain queued so a later successful flush persists them.
func TestFlushRequeuesOnFailure(t *testing.T) {
	f := &fakeStore{failFlush: true}
	b := newTestBuffer(f)
	b.Append(sample(1))
	b.Append(sample(2))

	if err := b.Flush(); err == nil {
		t.Fatal("expected flush error")
	}
	if got := b.BufferedRows()["d"]; got != 2 {
		t.Fatalf("after failed flush BufferedRows=%d, want 2 (rows must be re-queued, not discarded)", got)
	}

	// Recover: the same rows must now reach the store.
	f.failFlush = false
	if err := b.Flush(); err != nil {
		t.Fatalf("recovery flush: %v", err)
	}
	if f.flushed["d"] != 2 {
		t.Fatalf("store received %d rows, want 2", f.flushed["d"])
	}
	if got := b.BufferedRows()["d"]; got != 0 {
		t.Fatalf("after successful flush BufferedRows=%d, want 0", got)
	}
}

// New samples arriving during a failed flush must be ordered AFTER the
// re-queued (older) failed rows.
func TestFlushRequeuePreservesOrder(t *testing.T) {
	f := &fakeStore{failFlush: true}
	b := newTestBuffer(f)
	b.Append(sample(1))
	b.Append(sample(2))
	_ = b.Flush() // fails, re-queues 1,2
	b.Append(sample(3))

	b.mu.Lock()
	q := b.pending["d"]
	b.mu.Unlock()
	if len(q) != 3 || q[0].TsNs != 1 || q[2].TsNs != 3 {
		t.Fatalf("re-queue order wrong: %+v", q)
	}
}

func TestAppendCapDropsOldest(t *testing.T) {
	b := newTestBuffer(&fakeStore{})
	const extra = 5
	for i := int64(0); i < MaxPendingPerDevice+extra; i++ {
		b.Append(sample(i))
	}
	if got := b.BufferedRows()["d"]; got != MaxPendingPerDevice {
		t.Fatalf("BufferedRows=%d, want cap %d", got, MaxPendingPerDevice)
	}
	if got := b.RecordingState().DroppedRows; got != extra {
		t.Fatalf("DroppedRows=%d, want %d", got, extra)
	}
	b.mu.Lock()
	first := b.pending["d"][0].TsNs
	b.mu.Unlock()
	if first != extra {
		t.Fatalf("oldest surviving TsNs=%d, want %d (oldest %d dropped)", first, extra, extra)
	}
}

func TestRecordingStateDegradedThreshold(t *testing.T) {
	b := newTestBuffer(&fakeStore{})
	if b.RecordingState().Degraded {
		t.Fatal("fresh buffer should not be degraded")
	}
	for i := 0; i < 3; i++ {
		b.noteFlushErr(errors.New("boom"))
	}
	st := b.RecordingState()
	if !st.Degraded || st.ConsecutiveFailures != 3 || st.LastError == "" {
		t.Fatalf("after 3 failures: %+v", st)
	}
	b.noteFlushOK()
	st = b.RecordingState()
	if st.Degraded || st.ConsecutiveFailures != 0 || st.LastError != "" {
		t.Fatalf("after recovery: %+v", st)
	}
}
