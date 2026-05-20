// Package live keeps an in-memory snapshot of the latest sample from every
// data source so the web UI can poll it without contention on the writer
// paths. Snapshots are broadcast to subscribers on a single ticker rather
// than on every Publish, so a chatty 100 Hz IMU does not pin the goroutine
// scheduler.
package live

import (
	"sync"
	"time"
)

// Sample is one reading from a named source. Values are SI-natural floats
// keyed by channel/column name. TsNs is monotonic-ish wall-clock ns since
// the unix epoch as written by the producer.
type Sample struct {
	Device string             `json:"device"`
	TsNs   int64              `json:"ts_ns"`
	Values map[string]float64 `json:"values"`
}

// Snapshot is the latest sample per device, plus a server-side timestamp so
// the UI can show staleness.
type Snapshot struct {
	ServerTsNs int64             `json:"server_ts_ns"`
	Devices    map[string]Sample `json:"devices"`
}

type Hub struct {
	mu      sync.RWMutex
	latest  map[string]Sample
	subs    map[chan Snapshot]struct{}
	tickDur time.Duration
}

func NewHub() *Hub {
	return &Hub{
		latest:  map[string]Sample{},
		subs:    map[chan Snapshot]struct{}{},
		tickDur: 100 * time.Millisecond,
	}
}

// Publish stores `s` as the latest sample for its device. It does not block
// on subscriber channels.
func (h *Hub) Publish(s Sample) {
	h.mu.Lock()
	h.latest[s.Device] = s
	h.mu.Unlock()
}

// SnapshotNow returns a copy of the latest map for synchronous callers
// (e.g. an HTTP /api/status handler).
func (h *Hub) SnapshotNow() Snapshot {
	h.mu.RLock()
	defer h.mu.RUnlock()
	out := Snapshot{ServerTsNs: time.Now().UnixNano(), Devices: make(map[string]Sample, len(h.latest))}
	for k, v := range h.latest {
		out.Devices[k] = v
	}
	return out
}

// Subscribe returns a channel that receives a Snapshot every tick. Drop the
// returned channel into a select; on a slow consumer the hub drops snapshots
// rather than blocking the broadcast.
func (h *Hub) Subscribe() chan Snapshot {
	ch := make(chan Snapshot, 4)
	h.mu.Lock()
	h.subs[ch] = struct{}{}
	h.mu.Unlock()
	return ch
}

// Unsubscribe removes a channel; safe to call from the subscriber's own
// goroutine after it exits the select loop.
func (h *Hub) Unsubscribe(ch chan Snapshot) {
	h.mu.Lock()
	delete(h.subs, ch)
	h.mu.Unlock()
	close(ch)
}

// Run drives the broadcast ticker. Exits when ctx is done.
func (h *Hub) Run(stop <-chan struct{}) {
	t := time.NewTicker(h.tickDur)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			snap := h.SnapshotNow()
			h.mu.RLock()
			for ch := range h.subs {
				select {
				case ch <- snap:
				default:
				}
			}
			h.mu.RUnlock()
		}
	}
}
