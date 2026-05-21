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
}

// entry is the registry's per-reader bookkeeping. Each Update replaces
// the AttrView slice wholesale; only the writer mutex protects it. The
// reader is shared with the runOne goroutine; both serialize through the
// reader's own mutex.
type entry struct {
	reader Reader
	uiName string
	views  []AttrView
}

// Registry is a thread-safe map from IIO device name to a snapshot of its
// attribute table plus the live Reader, so the web layer can both read the
// current values and write changes back.
type Registry struct {
	mu      sync.RWMutex
	entries map[string]*entry
}

func NewRegistry() *Registry {
	return &Registry{entries: map[string]*entry{}}
}

// Register a reader under its IIO name. uiName is the disambiguated label
// (e.g. "icm20948_2") shown in the UI; Get/WriteAttr also accept it.
func (rg *Registry) Register(r Reader, uiName string) {
	rg.mu.Lock()
	defer rg.mu.Unlock()
	e := &entry{reader: r, uiName: uiName}
	rg.entries[r.Name()] = e
	if uiName != r.Name() {
		rg.entries[uiName] = e
	}
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
			Writable: e.reader.WritableAttr(rec.Channel, rec.Attr),
		}
		fb, hasFb := ChipFallback(chip, rec.Channel, rec.Attr)
		if hasFb && fb.ReadOnly {
			v.Writable = false
		}
		if v.Writable {
			v.Options = AttrOptions(e.reader, rec.Channel, rec.Attr)
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
	return out
}

// Names returns every registered device name (including UI aliases).
func (rg *Registry) Names() []string {
	rg.mu.RLock()
	defer rg.mu.RUnlock()
	out := make([]string, 0, len(rg.entries))
	for n := range rg.entries {
		out = append(out, n)
	}
	return out
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
	if !e.reader.WritableAttr(channel, attr) {
		return fmt.Errorf("registry: attr %s/%s not writable", channel, attr)
	}
	var err error
	if channel == "" {
		// go-iio doesn't expose SetAttr in our Reader; SetChannelAttr with
		// empty channel name writes the device-level file.
		err = e.reader.SetChannelAttr("", attr, value)
	} else {
		err = e.reader.SetChannelAttr(channel, attr, value)
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
	rg.Update(device, attrRecordsFor(e.reader))
	return nil
}

// attrRecordsFor uses SettingsAttrRecords when implemented (pod device).
func attrRecordsFor(r Reader) []store.AttrRecord {
	type snapshotter interface {
		SettingsAttrRecords() []store.AttrRecord
	}
	if s, ok := r.(snapshotter); ok {
		return s.SettingsAttrRecords()
	}
	return SnapshotAttrs(r)
}
