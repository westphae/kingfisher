package sensors

import (
	"context"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
)

// fakeReader implements Reader with a counter that ticks one value per
// channel per Read.
type fakeReader struct {
	name   string
	chs    []string
	closed atomic.Bool
	mu     sync.Mutex
	calls  int
}

func (f *fakeReader) Name() string       { return f.name }
func (f *fakeReader) Channels() []string { return f.chs }
func (f *fakeReader) ReadFloat(ch string) (float64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	return float64(f.calls), nil
}
func (f *fakeReader) ChannelAttr(ch, attr string) (string, error) { return "", os.ErrNotExist }
func (f *fakeReader) Attr(name string) (string, error)            { return "", os.ErrNotExist }
func (f *fakeReader) SetChannelAttr(ch, attr, v string) error     { return nil }
func (f *fakeReader) ReloadScale() error                          { return nil }
func (f *fakeReader) WritableAttr(ch, attr string) bool           { return false }
func (f *fakeReader) Close() error                                { f.closed.Store(true); return nil }

func (f *fakeReader) callCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls
}

// waitFor polls cond until it returns true or d elapses, returning the final result.
func waitFor(d time.Duration, cond func() bool) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if cond() {
			return true
		}
		time.Sleep(5 * time.Millisecond)
	}
	return cond()
}

func TestReaderEmitsAtConfiguredRateAndAppliesColumnRename(t *testing.T) {
	hub := live.NewHub()
	f := &fakeReader{name: "icm20948", chs: []string{"accel_x", "accel_y"}}
	cfg := &config.Config{
		Devices: map[string]config.Device{
			"icm20948": {
				Enabled:  true,
				SampleHz: 100,
				Channels: map[string]config.Channel{
					"accel_x": {Column: "ax"},
				},
			},
		},
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() {
		Run(ctx, config.NewHolder("", cfg), []Reader{f}, hub, nil, nil, nil)
		close(done)
	}()

	// Poll until the reader has emitted a few samples. A fixed time window is
	// flaky on a loaded Pi, where Run's startup filesystem work can eat most of
	// it; at 100Hz × 2 channels the count climbs fast once ticking begins.
	if !waitFor(2*time.Second, func() bool { return f.callCount() >= 5 }) {
		t.Errorf("too few reads at 100Hz: %d", f.callCount())
	}

	snap := hub.SnapshotNow()
	sm, ok := snap.Devices["icm20948"]
	if !ok {
		t.Fatalf("no icm20948 in snapshot: %+v", snap)
	}
	if _, ok := sm.Values["ax"]; !ok {
		t.Errorf("column override not applied; values=%v", sm.Values)
	}
	if _, ok := sm.Values["accel_y"]; !ok {
		t.Errorf("default column missing; values=%v", sm.Values)
	}

	cancel()
	<-done
	if !f.closed.Load() {
		t.Errorf("reader not closed after Run returned")
	}
}

func TestReaderRespectsEnabledFalse(t *testing.T) {
	hub := live.NewHub()
	f := &fakeReader{name: "bmp280", chs: []string{"pressure"}}
	cfg := &config.Config{
		Devices: map[string]config.Device{"bmp280": {Enabled: false}},
	}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	Run(ctx, config.NewHolder("", cfg), []Reader{f}, hub, nil, nil, nil)

	if f.calls != 0 {
		t.Errorf("disabled reader should not have been called; calls=%d", f.calls)
	}
	// Reader stays open while paused; Run closes it when the context ends.
	if !f.closed.Load() {
		t.Errorf("reader should be closed after Run returned")
	}
}

// TestReaderPauseResumeOnReload exercises the polled-path (runOne) lifecycle
// when a device is toggled off and back on via a live config reload: reads must
// stop while disabled and resume afterwards, without the goroutine exiting.
func TestReaderPauseResumeOnReload(t *testing.T) {
	hub := live.NewHub()
	f := &fakeReader{name: "bmp280", chs: []string{"pressure"}}
	enabled := &config.Config{
		Devices: map[string]config.Device{"bmp280": {Enabled: true, SampleHz: 50}},
	}
	disabled := &config.Config{
		Devices: map[string]config.Device{"bmp280": {Enabled: false, SampleHz: 50}},
	}
	holder := config.NewHolder("", enabled)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() {
		Run(ctx, holder, []Reader{f}, hub, nil, nil, nil)
		close(done)
	}()

	// Enabled: reads should accumulate. Poll rather than sleep a fixed budget —
	// Run does real filesystem work (hrtimer cleanup, attr snapshots) before the
	// reader goroutine ticks, which can outlast a short fixed sleep on a loaded Pi.
	if !waitFor(2*time.Second, func() bool { return f.callCount() > 0 }) {
		t.Fatalf("enabled reader never read")
	}

	// Disable: reads must stop. Wait for the reload to take effect (the read
	// count goes quiet), then confirm it stays quiet.
	holder.Set(disabled)
	var before int
	if !waitFor(2*time.Second, func() bool {
		n := f.callCount()
		time.Sleep(40 * time.Millisecond)
		if f.callCount() == n {
			before = n
			return true
		}
		return false
	}) {
		t.Fatalf("paused reader never stopped reading")
	}
	time.Sleep(120 * time.Millisecond)
	if after := f.callCount(); after != before {
		t.Errorf("paused reader kept reading: before=%d after=%d", before, after)
	}

	// Re-enable: reads must resume.
	holder.Set(enabled)
	if !waitFor(2*time.Second, func() bool { return f.callCount() > before }) {
		t.Errorf("resumed reader did not read again (was %d)", before)
	}

	cancel()
	<-done
	if !f.closed.Load() {
		t.Errorf("reader not closed after Run returned")
	}
}

// TestReaderResumeUsesNewRate guards against the stale-interval regression: if a
// device is disabled AND its sample_hz changed in the same reload, then later
// re-enabled at that new (higher) rate, the resumed reader must tick at the new
// rate — not the pre-disable rate. Counts are compared relatively (high window
// vs low window) so the assertion tolerates a loaded host.
func TestReaderResumeUsesNewRate(t *testing.T) {
	hub := live.NewHub()
	f := &fakeReader{name: "bmp280", chs: []string{"pressure"}}
	low := &config.Config{Devices: map[string]config.Device{"bmp280": {Enabled: true, SampleHz: 20}}}
	disabledFast := &config.Config{Devices: map[string]config.Device{"bmp280": {Enabled: false, SampleHz: 200}}}
	enabledFast := &config.Config{Devices: map[string]config.Device{"bmp280": {Enabled: true, SampleHz: 200}}}
	holder := config.NewHolder("", low)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() {
		Run(ctx, holder, []Reader{f}, hub, nil, nil, nil)
		close(done)
	}()

	// Measure the read count over a fixed window at the low (20 Hz) rate.
	if !waitFor(2*time.Second, func() bool { return f.callCount() > 0 }) {
		t.Fatalf("low-rate reader never read")
	}
	lowStart := f.callCount()
	time.Sleep(250 * time.Millisecond)
	lowWindow := f.callCount() - lowStart

	// Disable while changing the rate, then re-enable at the new rate.
	holder.Set(disabledFast)
	if !waitFor(2*time.Second, func() bool {
		n := f.callCount()
		time.Sleep(40 * time.Millisecond)
		return f.callCount() == n
	}) {
		t.Fatalf("reader never paused")
	}
	holder.Set(enabledFast)
	if !waitFor(2*time.Second, func() bool { return f.callCount() > lowStart+lowWindow }) {
		t.Fatalf("reader never resumed")
	}

	// Measure over the same window at the resumed (200 Hz) rate.
	highStart := f.callCount()
	time.Sleep(250 * time.Millisecond)
	highWindow := f.callCount() - highStart

	cancel()
	<-done

	// 200 Hz should yield ~10x the reads of 20 Hz; the stale-interval bug would
	// resume at 20 Hz and make the two windows comparable. 3x is a safe floor.
	if highWindow <= 3*lowWindow {
		t.Errorf("resumed rate not applied: low-window reads=%d high-window reads=%d (want high > 3x low)", lowWindow, highWindow)
	}
}
