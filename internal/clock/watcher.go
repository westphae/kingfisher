package clock

import (
	"context"
	"errors"
	"log"
	"math"
	"os"
	"sync"
	"time"

	"golang.org/x/sys/unix"

	"github.com/westphae/kingfisher/internal/store"
)

// Watcher maintains the flight DB's clock_offsets table: a piecewise mapping
// between CLOCK_REALTIME and CLOCK_MONOTONIC. It writes one anchor row at
// startup, then a row whenever the mapping shifts — detected two ways:
//
//   - a timerfd armed with TFD_TIMER_CANCEL_ON_SET fires the instant the
//     realtime clock is *stepped* (chrony makestep, date -s, anything), giving
//     microsecond-latency, source-agnostic step capture;
//   - a 1 Hz sampler catches gradual *slew* whenever accumulated drift since
//     the last recorded row exceeds the threshold (and doubles as the fallback
//     if timerfd is unavailable).
//
// It also polls chrony until first sync, then records clock_synced_at_utc /
// clock_true_start_utc metadata and hands the store the true session start so
// a fallback-named DB can be renamed at close. See docs/timestamps.md.
type Watcher struct {
	st        *store.Store
	threshold int64 // ns; slew shift that forces a row

	mu        sync.Mutex
	lastOffNs int64 // realtime−monotonic at the last recorded row

	startMonoNs int64
	synced      bool
}

// clockOffsetThreshold is sized against the highest sampling rate (50 Hz →
// 20 ms period): any mapping shift of a full sample period gets recorded.
const clockOffsetThreshold = 20 * time.Millisecond

func NewWatcher(st *store.Store) *Watcher {
	return &Watcher{st: st, threshold: int64(clockOffsetThreshold)}
}

// clockPair samples CLOCK_REALTIME and CLOCK_MONOTONIC back to back. The reads
// are not atomic; the µs of skew between them is far below threshold.
func clockPair() (wallNs, monoNs int64) {
	var tw, tm unix.Timespec
	_ = unix.ClockGettime(unix.CLOCK_REALTIME, &tw)
	_ = unix.ClockGettime(unix.CLOCK_MONOTONIC, &tm)
	return tw.Nano(), tm.Nano()
}

// begin writes the anchor row establishing the mapping at DB open.
func (w *Watcher) begin() {
	wallNs, monoNs := clockPair()
	w.mu.Lock()
	w.lastOffNs = wallNs - monoNs
	w.startMonoNs = monoNs
	w.mu.Unlock()
	if err := w.st.LogClockOffset(wallNs, monoNs, 0, "anchor"); err != nil {
		log.Printf("clock: anchor row: %v", err)
	}
}

// observe compares the current mapping against the last recorded row and
// appends a new row when it shifted ≥ threshold, or unconditionally when
// force is set (kernel-notified steps are discrete events worth a row even
// when small).
func (w *Watcher) observe(wallNs, monoNs int64, note string, force bool) {
	off := wallNs - monoNs
	w.mu.Lock()
	defer w.mu.Unlock()
	d := off - w.lastOffNs
	if !force && absNs(d) < w.threshold {
		return
	}
	if err := w.st.LogClockOffset(wallNs, monoNs, d, note); err != nil {
		log.Printf("clock: offset row (%s): %v", note, err)
		return
	}
	w.lastOffNs = off
	log.Printf("clock: %s %+.3fs — realtime↔monotonic mapping logged to clock_offsets", note, float64(d)/1e9)
}

func absNs(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}

// Run blocks until ctx is cancelled or stop is closed.
func (w *Watcher) Run(ctx context.Context, stop <-chan struct{}) {
	w.begin()

	done := make(chan struct{})
	defer close(done)
	if err := w.watchSteps(done); err != nil {
		log.Printf("clock: timerfd step listener unavailable (%v); relying on 1 Hz sampler only", err)
	}

	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	n := 0
	for {
		select {
		case <-ctx.Done():
			return
		case <-stop:
			return
		case <-tick.C:
			wallNs, monoNs := clockPair()
			w.observe(wallNs, monoNs, "slew", false)
			n++
			if !w.isSynced() && n%5 == 0 {
				w.checkSynced(ctx)
			}
		}
	}
}

func (w *Watcher) isSynced() bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.synced
}

// checkSynced polls chrony; on the first PPS/GPS lock it records sync + true
// session start metadata and hands the true start to the store (used to
// rename a fallback-named DB at close).
func (w *Watcher) checkSynced(ctx context.Context) {
	disc := QueryDiscipline(ctx)
	if !disc.Available || !disc.Synced {
		return
	}
	wallNs, monoNs := clockPair()
	trueStart := time.Unix(0, wallNs-(monoNs-w.startMonoNs)).UTC()
	w.mu.Lock()
	w.synced = true
	w.mu.Unlock()
	w.st.SetTrueStart(trueStart)
	_ = w.st.SetMeta("clock_synced_at_utc", time.Unix(0, wallNs).UTC().Format(time.RFC3339Nano))
	_ = w.st.SetMeta("clock_true_start_utc", trueStart.Format(time.RFC3339Nano))
	log.Printf("clock: first sync (%s); true session start %s", disc.Source, trueStart.Format(time.RFC3339))
}

// watchSteps arms a CLOCK_REALTIME timerfd with TFD_TIMER_CANCEL_ON_SET at a
// far-future expiry. The kernel cancels it (read fails ECANCELED) the moment
// the realtime clock is stepped, from any source. Slews do not fire it.
func (w *Watcher) watchSteps(done <-chan struct{}) error {
	fd, err := unix.TimerfdCreate(unix.CLOCK_REALTIME, unix.TFD_NONBLOCK|unix.TFD_CLOEXEC)
	if err != nil {
		return err
	}
	arm := func() error {
		return unix.TimerfdSettime(fd, unix.TFD_TIMER_ABSTIME|unix.TFD_TIMER_CANCEL_ON_SET,
			&unix.ItimerSpec{Value: unix.Timespec{Sec: math.MaxInt32}}, nil)
	}
	if err := arm(); err != nil {
		unix.Close(fd)
		return err
	}
	f := os.NewFile(uintptr(fd), "clock-step-timerfd") // nonblocking → netpoller; Close unblocks Read
	go func() {
		<-done
		f.Close()
	}()
	go func() {
		buf := make([]byte, 8)
		for {
			_, err := f.Read(buf)
			switch {
			case err == nil:
				continue // expiry (never, at MaxInt32) — ignore
			case errors.Is(err, unix.ECANCELED):
				wallNs, monoNs := clockPair()
				w.observe(wallNs, monoNs, "step", true)
				if err := arm(); err != nil {
					log.Printf("clock: re-arm step listener: %v", err)
					return
				}
			default:
				return // closed on shutdown
			}
		}
	}()
	return nil
}
