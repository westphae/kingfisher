package store

import (
	"log"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/westphae/kingfisher/internal/live"
)

// MaxPendingPerDevice caps how many unflushed rows we keep per device.
// If flushes fail (full disk, locked DB) and pending grows past this cap,
// we drop the OLDEST rows in that device's queue and bump droppedRows so
// the failure is visible in the status surface instead of consuming all
// RAM on a long flight. Sized for ~5 minutes at 100 Hz per device.
const MaxPendingPerDevice = 30000

// flushTarget is the subset of *Store the buffer needs. Kept as an
// interface so the failure paths (re-queue on flush error, degraded
// surfacing) can be unit-tested with a fake that fails on demand.
type flushTarget interface {
	EnsureTable(device string, columns []string) error
	FlushBatch(device string, columns []string, samples []live.Sample) error
	CheckpointWAL() error
}

// Buffer batches Samples per device and flushes every FlushInterval in a
// single transaction. EnsureTable is called automatically on first sight of
// a device or column.
type Buffer struct {
	store         flushTarget
	flushInterval time.Duration
	paused        atomic.Bool

	droppedRows atomic.Uint64

	// flushMu guards lastFlushErr AND consecutiveFailures together so
	// RecordingState always returns a coherent (count, error) pair.
	flushMu             sync.Mutex
	lastFlushErr        string
	consecutiveFailures int

	mu      sync.Mutex
	pending map[string][]live.Sample // device → rows
	cols    map[string]map[string]bool
	colsOrd map[string][]string
}

// RecordingState is a snapshot of the buffer's health for the cockpit UI.
// degraded is true once we've seen ≥3 consecutive flush failures — the UI
// uses that to surface a REC ERROR badge so a pilot notices a stuck
// writer instead of discovering it only after landing.
type RecordingState struct {
	Paused              bool   `json:"paused"`
	Degraded            bool   `json:"degraded"`
	LastError           string `json:"last_error,omitempty"`
	ConsecutiveFailures int32  `json:"consecutive_failures"`
	DroppedRows         uint64 `json:"dropped_rows"`
}

// RecordingState returns a copy of the current recording health snapshot.
func (b *Buffer) RecordingState() RecordingState {
	b.flushMu.Lock()
	last := b.lastFlushErr
	fails := b.consecutiveFailures
	b.flushMu.Unlock()
	return RecordingState{
		Paused:              b.paused.Load(),
		Degraded:            fails >= 3,
		LastError:           last,
		ConsecutiveFailures: int32(fails),
		DroppedRows:         b.droppedRows.Load(),
	}
}

// SetPaused toggles recording. While paused, Append drops incoming samples
// (so the buffer doesn't grow unbounded) but the live hub still receives
// them so the UI keeps showing fresh values. When pausing, pending rows are
// flushed and the WAL is checkpointed into the main DB file.
func (b *Buffer) SetPaused(p bool) error {
	b.paused.Store(p)
	if !p {
		return nil
	}
	if err := b.Flush(); err != nil {
		b.noteFlushErr(err)
		return err
	}
	b.noteFlushOK()
	return b.store.CheckpointWAL()
}

// Paused reports the current pause state.
func (b *Buffer) Paused() bool { return b.paused.Load() }

func NewBuffer(s *Store, flushInterval time.Duration) *Buffer {
	return &Buffer{
		store:         s,
		flushInterval: flushInterval,
		pending:       map[string][]live.Sample{},
		cols:          map[string]map[string]bool{},
		colsOrd:       map[string][]string{},
	}
}

// Append queues a sample for later flush. It is safe to call from many
// goroutines. While paused, the sample is silently dropped.
// If a device's pending queue is already at MaxPendingPerDevice (because
// flushes are failing), the oldest row is shifted out and droppedRows is
// incremented so the loss is visible in the status surface rather than
// growing pending unbounded.
func (b *Buffer) Append(sm live.Sample) {
	if sm.Device == "" {
		return
	}
	if b.paused.Load() {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	queue := b.pending[sm.Device]
	if len(queue) >= MaxPendingPerDevice {
		// Drop oldest. A slice copy keeps the backing array bounded
		// over time; otherwise repeated re-slicing leaks memory.
		drop := len(queue) - MaxPendingPerDevice + 1
		queue = append(queue[:0], queue[drop:]...)
		b.droppedRows.Add(uint64(drop))
	}
	b.pending[sm.Device] = append(queue, sm)
	set, ok := b.cols[sm.Device]
	if !ok {
		set = map[string]bool{}
		b.cols[sm.Device] = set
	}
	for k := range sm.Values {
		c := Sanitize(k)
		if c == "" {
			continue
		}
		if !set[c] {
			set[c] = true
			b.colsOrd[sm.Device] = append(b.colsOrd[sm.Device], c)
		}
	}
}

// BufferedRows reports the count of unflushed rows per device. Used by the
// /api/status endpoint.
func (b *Buffer) BufferedRows() map[string]int {
	b.mu.Lock()
	defer b.mu.Unlock()
	out := make(map[string]int, len(b.pending))
	for d, p := range b.pending {
		out[d] = len(p)
	}
	return out
}

// Flush drains every pending device into the DB. Each device is one tx.
func (b *Buffer) Flush() error {
	b.mu.Lock()
	if len(b.pending) == 0 {
		b.mu.Unlock()
		return nil
	}
	pending := b.pending
	cols := b.colsOrd
	b.pending = map[string][]live.Sample{}
	// Keep the cols/colsOrd maps so column ordering is stable across flushes.
	b.mu.Unlock()

	// Sort device names for deterministic flush order in logs.
	devs := make([]string, 0, len(pending))
	for d := range pending {
		devs = append(devs, d)
	}
	sort.Strings(devs)

	for i, d := range devs {
		samples := pending[d]
		colList := cols[d]
		if err := b.store.EnsureTable(d, colList); err != nil {
			// Re-queue the failing device and everything not yet
			// attempted so a transient write error (full disk, locked
			// DB) doesn't silently discard buffered rows. The pending
			// swap above already exposed an empty map to Append, so
			// new samples may have arrived; the failed (older) rows are
			// prepended ahead of them.
			b.requeue(pending, devs[i:])
			return err
		}
		if err := b.store.FlushBatch(d, colList, samples); err != nil {
			b.requeue(pending, devs[i:])
			return err
		}
	}
	return nil
}

// Run loops on the flush ticker and exits on stop. Errors are logged and
// tracked so the UI can show a REC ERROR badge after sustained failures.
func (b *Buffer) Run(stop <-chan struct{}) {
	t := time.NewTicker(b.flushInterval)
	defer t.Stop()
	for {
		select {
		case <-stop:
			if err := b.Flush(); err != nil {
				log.Printf("store: final flush: %v", err)
				b.noteFlushErr(err)
			} else {
				b.noteFlushOK()
			}
			return
		case <-t.C:
			if err := b.Flush(); err != nil {
				log.Printf("store: flush: %v", err)
				b.noteFlushErr(err)
			} else {
				b.noteFlushOK()
			}
		}
	}
}

// requeue merges un-flushed device rows back into b.pending after a flush
// failure, prepending the failed (older) rows ahead of any samples that
// arrived during the flush, and re-applying the per-device cap so memory
// stays bounded under sustained failure (dropping oldest, counted).
func (b *Buffer) requeue(pending map[string][]live.Sample, devs []string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	for _, d := range devs {
		old := pending[d]
		if len(old) == 0 {
			continue
		}
		merged := append(old, b.pending[d]...)
		if len(merged) > MaxPendingPerDevice {
			drop := len(merged) - MaxPendingPerDevice
			b.droppedRows.Add(uint64(drop))
			merged = merged[drop:]
		}
		b.pending[d] = merged
	}
}

func (b *Buffer) noteFlushErr(err error) {
	b.flushMu.Lock()
	b.consecutiveFailures++
	b.lastFlushErr = err.Error()
	b.flushMu.Unlock()
}

func (b *Buffer) noteFlushOK() {
	b.flushMu.Lock()
	if b.consecutiveFailures != 0 {
		b.consecutiveFailures = 0
		b.lastFlushErr = ""
	}
	b.flushMu.Unlock()
}
