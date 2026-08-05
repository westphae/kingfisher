// Package sensors discovers IIO devices and runs one reader goroutine per
// device. Buffered capture (kernel FIFO + hrtimer) is the default when
// scan_elements exist; otherwise each channel is polled on a ticker at
// sample_hz. Samples go to the live hub and store buffer.
package sensors

import (
	"context"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/westphae/go-iio"
	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/location"
	"github.com/westphae/kingfisher/internal/store"
	"github.com/westphae/kingfisher/internal/units"
)

// Reader abstracts the bits of *iio.Device the readers use so tests can
// drop in a fake. Implementations must be safe for concurrent use by the
// reader goroutine and the web layer (registry attr writes).
type Reader interface {
	Name() string
	Channels() []string
	ReadFloat(ch string) (float64, error)
	ChannelAttr(ch, attr string) (string, error)
	Attr(name string) (string, error)
	SetChannelAttr(ch, attr, value string) error
	ReloadScale() error
	// WritableAttr reports whether the sysfs file backing this attribute is
	// writable. Channel-level attrs live at in_<channel>_<attr>; device-level
	// attrs (channel=="") live at <attr>. Returns false if the file does not
	// exist.
	WritableAttr(ch, attr string) bool
	Close() error
}

// iioReader is the production wrapper around *iio.Device. All entry points
// take the per-reader mutex because *iio.Device is not safe for concurrent
// use, and the registry's WriteAttr can race with the reader goroutine's
// ReadFloat.
type iioReader struct {
	mu   sync.Mutex
	d    *iio.Device
	name string // captured from DeviceInfo at Open time
	path string // sysfs path, used for writability probes
}

func (r *iioReader) Name() string { return r.name }
func (r *iioReader) Channels() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	chs := r.d.Channels()
	out := make([]string, 0, len(chs))
	for _, c := range chs {
		out = append(out, c.Name())
	}
	return out
}
func (r *iioReader) ReadFloat(ch string) (float64, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.d.ReadFloat(ch)
}
func (r *iioReader) ChannelAttr(ch, attr string) (string, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.d.ChannelAttr(ch, attr)
}
func (r *iioReader) Attr(name string) (string, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.d.Attr(name)
}
func (r *iioReader) SetChannelAttr(ch, attr, v string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.d.SetChannelAttr(ch, attr, v)
}
func (r *iioReader) ReloadScale() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.d.ReloadScale()
}
func (r *iioReader) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.d.Close()
}
func (r *iioReader) WritableAttr(ch, attr string) bool {
	if r.path == "" {
		return false
	}
	var fname string
	if ch == "" {
		fname = filepath.Join(r.path, attr)
	} else {
		fname = filepath.Join(r.path, "in_"+ch+"_"+attr)
	}
	info, err := os.Stat(fname)
	if err != nil {
		return false
	}
	// Owner OR group write bit. IIO sysfs files are usually root:plugdev
	// with 0664; treat group-write as sufficient since kingfisher runs as a
	// user in the plugdev group.
	return info.Mode().Perm()&0o220 != 0
}

func (r *iioReader) hasPath(rel string) bool {
	if r.path == "" {
		return false
	}
	_, err := os.Stat(filepath.Join(r.path, rel))
	return err == nil
}

// Open discovers everything iio.Discover sees and returns one Reader per
// device. Errors opening individual devices are logged and skipped.
// iio.OpenPath does not populate the device name on the handle (only Addr),
// so we capture the name from the DeviceInfo here.
func Open() ([]Reader, error) {
	infos, err := iio.Discover()
	if err != nil {
		return nil, fmt.Errorf("iio.Discover: %w", err)
	}
	out := make([]Reader, 0, len(infos))
	for _, info := range infos {
		log.Printf("sensors: opening %s (%s)", info.Name, info.Path)
		d, err := iio.OpenPath(info.Path)
		if err != nil {
			log.Printf("sensors: open %s (%s): %v", info.Name, info.Path, err)
			continue
		}
		out = append(out, &iioReader{d: d, name: info.Name, path: info.Path})
	}
	return out, nil
}

// uniqueName allocates a stable per-process name when multiple kernel
// devices report the same Name(): "icm20948", "icm20948_2", "icm20948_3"…
type nameAllocator struct {
	seen map[string]int
}

func newNameAllocator() *nameAllocator { return &nameAllocator{seen: map[string]int{}} }
func (a *nameAllocator) Next(base string) string {
	if base == "" {
		base = "device"
	}
	n := a.seen[base]
	a.seen[base] = n + 1
	if n == 0 {
		return base
	}
	return fmt.Sprintf("%s_%d", base, n+1)
}

// Run starts a reader goroutine per Reader. Each respects its device's
// SampleHz from the holder's config and applies any configured channel
// attribute writes. Cancel ctx to stop all readers; Run blocks until every
// goroutine exits. The store, if non-nil, receives sensor-attribute
// snapshots at startup and on config reload. The registry, if non-nil,
// receives the same snapshots so the web layer can read+write attrs.
func Run(ctx context.Context, holder *config.Holder, readers []Reader, hub *live.Hub, buf *store.Buffer, st *store.Store, reg *Registry) {
	cleanupKingfisherHRTimers()
	alloc := newNameAllocator()
	cfg := holder.Get()
	var wg sync.WaitGroup
	// One name per discovered reader, decided up front so the names stay
	// stable across config edits.
	type runCtx struct {
		r    Reader
		name string
	}
	var active []runCtx
	for _, r := range readers {
		name := alloc.Next(r.Name())
		dev := cfg.DeviceOrDefault(r.Name(), 10)
		if !dev.Enabled {
			log.Printf("sensors: %s disabled by config (paused until enabled)", name)
		}
		active = append(active, runCtx{r: r, name: name})
	}
	// Register IIO device names with the config holder so other components
	// (e.g. the /api/devices endpoint) can enumerate them without needing
	// the discovery list.
	names := make([]string, 0, len(active))
	for _, a := range active {
		names = append(names, a.r.Name())
	}
	holder.SetIIODeviceNames(names)

	for _, a := range active {
		if reg != nil {
			reg.Register(a.r, a.name, location.Hub)
		}
		if err := applyConfiguredAttrs(a.r, cfg.DeviceOrDefault(a.r.Name(), 10)); err != nil {
			log.Printf("sensors: %s attrs: %v", a.name, err)
		}
		// Snapshot once before any data ticks so the DB has the "as flown"
		// configuration even if the user never touches the UI.
		recs := SnapshotAttrs(a.r)
		if st != nil {
			if err := st.LogAttrs(a.r.Name(), location.Hub, recs); err != nil {
				log.Printf("sensors: %s log attrs: %v", a.name, err)
			}
		}
		if reg != nil {
			reg.Update(a.r.Name(), recs)
		}
		wg.Add(1)
		go func(r Reader, name string) {
			defer wg.Done()
			defer r.Close()
			if ir, ok := r.(*iioReader); ok && cfg.DeviceOrDefault(r.Name(), 10).WantBuffer(len(ir.bufferChannels())) {
				runBuffered(ctx, ir, name, holder, hub, buf, st, reg)
			} else {
				runOne(ctx, r, name, holder, hub, buf, st, reg)
			}
		}(a.r, a.name)
	}
	wg.Wait()
}

func applyConfiguredAttrs(r Reader, dev config.Device) error {
	if len(dev.Attrs) == 0 && len(dev.Channels) == 0 {
		return nil
	}
	var firstErr error
	set := func(k, v string) {
		ch, attr := SplitIIOAttr(k)
		if err := r.SetChannelAttr(ch, attr, v); err != nil && firstErr == nil {
			firstErr = fmt.Errorf("setattr %s=%s: %w", k, v, err)
		}
	}
	// ODR before UI LPF: filter values are absolute Hz derived from current ODR.
	if v, ok := dev.Attrs["sampling_frequency"]; ok {
		set("sampling_frequency", v)
	}
	for k, v := range dev.Attrs {
		if k == "sampling_frequency" {
			continue
		}
		set(k, v)
	}
	if err := r.ReloadScale(); err != nil && firstErr == nil {
		firstErr = fmt.Errorf("reload scale: %w", err)
	}
	return firstErr
}

// iioAttrSuffixes are known IIO channel attr suffixes, longest-first, so
// "in_anglvel_x_calibbias" splits as channel=anglvel_x (not anglvel / x_calibbias).
var iioAttrSuffixes = []string{
	"filter_low_pass_3db_frequency",
	"oversampling_ratio",
	"sampling_frequency",
	"integration_time",
	"calibbias",
	"calibscale",
	"scale",
	"offset",
	"raw",
	"input",
}

// SplitIIOAttr turns "in_anglvel_filter_low_pass_3db_frequency" into
// (channel="anglvel", attr="filter_low_pass_3db_frequency") and
// "in_anglvel_x_calibbias" into (channel="anglvel_x", attr="calibbias").
// Keys that don't start with "in_" are treated as device-level attrs (channel="").
func SplitIIOAttr(full string) (channel, attr string) {
	if !strings.HasPrefix(full, "in_") {
		return "", full
	}
	s := strings.TrimPrefix(full, "in_")
	for _, suf := range iioAttrSuffixes {
		if strings.HasSuffix(s, "_"+suf) {
			return strings.TrimSuffix(s, "_"+suf), suf
		}
	}
	i := strings.IndexByte(s, '_')
	if i <= 0 {
		return s, ""
	}
	return s[:i], s[i+1:]
}

// JoinIIOAttr is the inverse: ("accel", "scale") -> "in_accel_scale";
// ("", "sampling_frequency") -> "sampling_frequency". Used when persisting
// a UI-driven attribute write back to the config file.
func JoinIIOAttr(channel, attr string) string {
	if channel == "" {
		return attr
	}
	return "in_" + channel + "_" + attr
}

// logAttrDiff snapshots r's current attrs, records any change versus prev to
// the store, and refreshes the registry. It returns the fresh snapshot to
// become the caller's new prev. Shared by the polled and buffered reload paths.
func logAttrDiff(r Reader, st *store.Store, reg *Registry, name string, prev []store.AttrRecord) []store.AttrRecord {
	curr := SnapshotAttrs(r)
	diff := DiffAttrs(prev, curr)
	if len(diff) > 0 && st != nil {
		if err := st.LogAttrs(r.Name(), location.Hub, diff); err != nil {
			log.Printf("sensors: %s log attr diff: %v", name, err)
		}
	}
	if reg != nil {
		reg.Update(r.Name(), curr)
	}
	return curr
}

func runOne(ctx context.Context, r Reader, name string, holder *config.Holder, hub *live.Hub, buf *store.Buffer, st *store.Store, reg *Registry) {
	reload := holder.Subscribe()
	cfg := holder.Get()
	dev := cfg.DeviceOrDefault(r.Name(), 10)
	channels := r.Channels()
	colMap := buildColumnMap(channels, dev.Channels)

	interval := tickInterval(dev.SampleHz)
	t := time.NewTicker(interval)
	defer t.Stop()
	if !dev.Enabled {
		t.Stop() // start idle when disabled; reload resumes via t.Reset
	}
	prevAttrs := SnapshotAttrs(r)

	for {
		select {
		case <-ctx.Done():
			return
		case <-reload:
			cfg = holder.Get()
			newDev := cfg.DeviceOrDefault(r.Name(), 10)
			if !newDev.Enabled {
				log.Printf("sensors: %s disabled by config reload — pausing", name)
				t.Stop() // go idle; the <-t.C guard swallows any stale tick
				dev = newDev
				continue
			}
			if !dev.Enabled {
				log.Printf("sensors: %s enabled by config reload — resuming", name)
				// The pause branch advanced dev.SampleHz without touching
				// interval, so recompute from the live rate — otherwise a
				// disable-with-rate-change followed by re-enable resumes at the
				// stale rate (the guard below sees newDev.SampleHz == dev.SampleHz
				// and skips). Drain any tick buffered before the pause Stop so the
				// first post-resume sample isn't emitted early.
				interval = tickInterval(newDev.SampleHz)
				select {
				case <-t.C:
				default:
				}
				t.Reset(interval)
			}
			if newDev.SampleHz != dev.SampleHz && newDev.SampleHz > 0 {
				interval = tickInterval(newDev.SampleHz)
				t.Reset(interval)
				log.Printf("sensors: %s rate -> %g Hz", name, newDev.SampleHz)
			}
			if err := applyConfiguredAttrs(r, newDev); err != nil {
				log.Printf("sensors: %s reapply attrs: %v", name, err)
			}
			prevAttrs = logAttrDiff(r, st, reg, name, prevAttrs)
			dev = newDev
			colMap = buildColumnMap(channels, dev.Channels)
		case <-t.C:
			if !dev.Enabled {
				continue
			}
			values := make(map[string]float64, len(channels))
			for _, ch := range channels {
				v, err := r.ReadFloat(ch)
				if err != nil {
					continue
				}
				v = units.NormalizeIIO(ch, v)
				col := colMap[ch]
				if canon := units.ColumnForIIO(ch); canon != "" {
					col = canon
				}
				values[col] = v
			}
			if len(values) == 0 {
				continue
			}
			sm := live.Sample{Device: name, TsNs: time.Now().UnixNano(), Values: values}
			hub.Publish(sm)
			if buf != nil {
				buf.Append(sm)
			}
		}
	}
}

func tickInterval(hz float64) time.Duration {
	if hz <= 0 {
		hz = 10
	}
	return time.Duration(float64(time.Second) / hz)
}

// buildColumnMap maps each IIO channel name to its output column name,
// honouring user overrides from cfg and falling back to the sanitised IIO
// name.
func buildColumnMap(channels []string, overrides map[string]config.Channel) map[string]string {
	out := make(map[string]string, len(channels))
	for _, ch := range channels {
		col := store.Sanitize(ch)
		if o, ok := overrides[ch]; ok && o.Column != "" {
			col = store.Sanitize(o.Column)
		}
		out[ch] = col
	}
	return out
}
