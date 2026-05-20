package store

import (
	"log"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/westphae/kingfisher/internal/live"
)

// Buffer batches Samples per device and flushes every FlushInterval in a
// single transaction. EnsureTable is called automatically on first sight of
// a device or column.
type Buffer struct {
	store         *Store
	flushInterval time.Duration
	paused        atomic.Bool

	mu      sync.Mutex
	pending map[string][]live.Sample // device → rows
	cols    map[string]map[string]bool
	colsOrd map[string][]string
}

// SetPaused toggles recording. While paused, Append drops incoming samples
// (so the buffer doesn't grow unbounded) but the live hub still receives
// them so the UI keeps showing fresh values. Rows queued before the pause
// are still flushed on the normal cadence.
func (b *Buffer) SetPaused(p bool) { b.paused.Store(p) }

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
func (b *Buffer) Append(sm live.Sample) {
	if sm.Device == "" {
		return
	}
	if b.paused.Load() {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.pending[sm.Device] = append(b.pending[sm.Device], sm)
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

	for _, d := range devs {
		samples := pending[d]
		colList := cols[d]
		if err := b.store.EnsureTable(d, colList); err != nil {
			return err
		}
		if err := b.store.FlushBatch(d, colList, samples); err != nil {
			return err
		}
	}
	return nil
}

// Run loops on the flush ticker and exits on stop. Errors are logged.
func (b *Buffer) Run(stop <-chan struct{}) {
	t := time.NewTicker(b.flushInterval)
	defer t.Stop()
	for {
		select {
		case <-stop:
			if err := b.Flush(); err != nil {
				log.Printf("store: final flush: %v", err)
			}
			return
		case <-t.C:
			if err := b.Flush(); err != nil {
				log.Printf("store: flush: %v", err)
			}
		}
	}
}
