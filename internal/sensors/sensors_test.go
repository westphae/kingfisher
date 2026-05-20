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
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	Run(ctx, config.NewHolder("", cfg), []Reader{f}, hub, nil, nil, nil)

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
	// At 100Hz for ~200ms we expect roughly 20 ticks * 2 channels = ~40
	// calls. Allow wide slack for CI jitter.
	if f.calls < 5 {
		t.Errorf("too few reads at 100Hz over 200ms: %d", f.calls)
	}
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
	if !f.closed.Load() {
		t.Errorf("disabled reader should still be closed")
	}
}
