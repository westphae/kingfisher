package sensors

import (
	"fmt"
	"sync"

	"github.com/westphae/kingfisher/internal/store"
)

// AttrView is one row of the per-sensor settings table the UI displays.
// Writable means the underlying sysfs file is writable; Options is the
// parsed contents of the sibling `_available` file (nil if absent or a
// continuous range). The UI renders a dropdown when Options is non-empty.
type AttrView struct {
	Channel  string   `json:"channel"`
	Attr     string   `json:"attr"`
	Value    string   `json:"value"`
	Writable bool     `json:"writable"`
	Options  []string `json:"options,omitempty"`
	Location string   `json:"location,omitempty"`
}

// entry is the registry's per-reader bookkeeping. Each Update replaces
// the AttrView slice wholesale; only the writer mutex protects it. The
// reader is shared with the runOne goroutine; both serialize through the
// reader's own mutex.
type entry struct {
	reader   Reader
	uiName   string
	location string
	views    []AttrView
}

// Registry is a thread-safe map from IIO device name to a snapshot of its
// attribute table plus the live Reader, so the web layer can both read the
// current values and write changes back.
type Registry struct {
	mu      sync.RWMutex
	entries map[string]*entry
	gate    *BufferGate
}

func NewRegistry() *Registry {
	return &Registry{entries: map[string]*entry{}, gate: NewBufferGate()}
}

// Gate returns the buffer quiesce coordinator (never nil after NewRegistry).
func (rg *Registry) Gate() *BufferGate {
	if rg == nil {
		return nil
	}
	return rg.gate
}

// Register a reader under its IIO name. uiName is the disambiguated label
// (e.g. "icm20948_2") shown in the UI; Get/WriteAttr also accept it.
func (rg *Registry) Register(r Reader, uiName, loc string) {
	rg.mu.Lock()
	defer rg.mu.Unlock()
	e := &entry{reader: r, uiName: uiName, location: loc}
	rg.entries[r.Name()] = e
	if uiName != r.Name() {
		rg.entries[uiName] = e
	}
}

// RegisterAlias adds another device name for an already-registered reader
// (e.g. bmp581 → pod reader). Each alias gets its own cached views so
// per-tab attr snapshots do not clobber each other.
func (rg *Registry) RegisterAlias(device string, r Reader, loc string) {
	rg.mu.Lock()
	defer rg.mu.Unlock()
	if rg.entries[r.Name()] == nil {
		rg.entries[r.Name()] = &entry{reader: r, uiName: device, location: loc}
	}
	rg.entries[device] = &entry{reader: r, uiName: device, location: loc}
}

// Location returns "hub", "pod", or "" if the device is unknown.
func (rg *Registry) Location(device string) string {
	rg.mu.RLock()
	defer rg.mu.RUnlock()
	if e := rg.entries[device]; e != nil {
		return e.location
	}
	return ""
}

// Update replaces the cached attr snapshot for `device`. Pass the result of
// SnapshotAttrs and the registry will probe each for writability.
func (rg *Registry) Update(device string, recs []store.AttrRecord) {
	rg.mu.RLock()
	e := rg.entries[device]
	rg.mu.RUnlock()
	if e == nil {
		return
	}
	chip := e.reader.Name()
	views := make([]AttrView, 0, len(recs))
	for _, rec := range recs {
		v := AttrView{
			Channel:  rec.Channel,
			Attr:     rec.Attr,
			Value:    rec.Value,
			Writable: writableFor(e, rec),
			Location: e.location,
		}
		fb, hasFb := ChipFallback(chip, rec.Channel, rec.Attr)
		if hasFb && fb.ReadOnly {
			v.Writable = false
		}
		if v.Writable {
			optCh := rec.Channel
			if optCh == "" {
				optCh = e.uiName
			}
			v.Options = AttrOptions(e.reader, optCh, rec.Attr)
			if len(v.Options) == 0 && hasFb {
				v.Options = fb.Options
			}
		}
		views = append(views, v)
	}
	rg.mu.Lock()
	e.views = views
	rg.mu.Unlock()
}

// Get returns the most recent attr snapshot for `device`, or nil if no
// such device.
func (rg *Registry) Get(device string) []AttrView {
	rg.mu.RLock()
	defer rg.mu.RUnlock()
	e := rg.entries[device]
	if e == nil {
		return nil
	}
	// return a defensive copy so callers can serialize without holding the
	// lock.
	out := make([]AttrView, len(e.views))
	copy(out, e.views)
	for i := range out {
		if out[i].Location == "" {
			out[i].Location = e.location
		}
	}
	return out
}

// MaxBufferedHzFor returns the sustainable buffered rate for a registered
// kernel device name, or (0, false) if unknown / not registered.
func (rg *Registry) MaxBufferedHzFor(kernelName string) (float64, bool) {
	rg.mu.RLock()
	e := rg.entries[kernelName]
	rg.mu.RUnlock()
	if e == nil {
		return 0, false
	}
	return MaxBufferedHz(e.reader)
}

// ChannelAttr reads one channel attribute from the live reader.
func (rg *Registry) ChannelAttr(device, channel, attr string) (string, error) {
	rg.mu.RLock()
	e := rg.entries[device]
	rg.mu.RUnlock()
	if e == nil {
		return "", fmt.Errorf("registry: unknown device %q", device)
	}
	return e.reader.ChannelAttr(channel, attr)
}

// WriteAttr writes one attribute on the device's reader. channel may be
// empty for device-level attrs. Returns an error if the device is unknown
// or the attr is not writable.
func (rg *Registry) WriteAttr(device, channel, attr, value string) error {
	rg.mu.RLock()
	e := rg.entries[device]
	rg.mu.RUnlock()
	if e == nil {
		return fmt.Errorf("registry: unknown device %q", device)
	}
	rec := store.AttrRecord{Channel: channel, Attr: attr}
	if !writableFor(e, rec) {
		return fmt.Errorf("registry: attr %s/%s not writable", channel, attr)
	}
	ch := channel
	if ch == "" {
		ch = device
	}
	var err error
	if ch == "" {
		err = e.reader.SetChannelAttr("", attr, value)
	} else {
		err = e.reader.SetChannelAttr(ch, attr, value)
	}
	if err != nil {
		return err
	}
	// scale and offset are cross-coupled with raw decode; trigger reload so
	// subsequent buffered reads pick up the new factors.
	if attr == "scale" || attr == "offset" {
		_ = e.reader.ReloadScale()
	}
	// Refresh the cached snapshot so the UI sees the new value immediately.
	rg.Update(device, attrRecordsFor(e.reader, device))
	return nil
}

func writableFor(e *entry, rec store.AttrRecord) bool {
	type perDevice interface {
		WritableForDevice(device, channel, attr string) bool
	}
	if d, ok := e.reader.(perDevice); ok {
		return d.WritableForDevice(e.uiName, rec.Channel, rec.Attr)
	}
	return e.reader.WritableAttr(rec.Channel, rec.Attr)
}

// attrRecordsFor uses SettingsAttrRecords when implemented (pod device).
func attrRecordsFor(r Reader, uiDevice string) []store.AttrRecord {
	type perUIDevice interface {
		SettingsAttrRecordsForUIDevice(string) []store.AttrRecord
	}
	if s, ok := r.(perUIDevice); ok {
		if recs := s.SettingsAttrRecordsForUIDevice(uiDevice); recs != nil {
			return recs
		}
	}
	type snapshotter interface {
		SettingsAttrRecords() []store.AttrRecord
	}
	if s, ok := r.(snapshotter); ok {
		return s.SettingsAttrRecords()
	}
	return SnapshotAttrs(r)
}
