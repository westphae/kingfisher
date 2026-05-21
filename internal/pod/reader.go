package pod

import (
	"fmt"
	"strconv"
	"strings"
	"sync"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
	"github.com/westphae/kingfisher/internal/store"
)

// DeviceName is the live.Sample.Device value the pod publishes under. It
// also doubles as the registry key — `/api/devices/pod/attrs` resolves
// here.
const DeviceName = "pod"

// channel names exposed on the pod device. They land as columns in the
// flight DB (sanitised) and as keys in the WS feed.
const (
	ChAirspeedDP   = "airspeed_dp"
	ChAirspeedTemp = "airspeed_temp"
	ChStaticP      = "static_p"
	ChStaticTemp   = "static_temp"
	ChMagX         = "mag_x"
	ChMagY         = "mag_y"
	ChMagZ         = "mag_z"
)

var podChannels = []string{
	ChAirspeedDP, ChAirspeedTemp,
	ChStaticP, ChStaticTemp,
	ChMagX, ChMagY, ChMagZ,
}

// sensorSettingsChannel is the UI label for per-sensor rate control. The pod
// firmware sets one Hz per physical sensor (MMC5983, BMP581, MS4525), not per
// data channel — mag_x/y/z and static_p/temp share the same rate.
var sensorSettingsChannel = map[wire.SensorID]string{
	wire.SensorAirspeed: "airspeed",
	wire.SensorStatic:   "static",
	wire.SensorMag:      "mag",
}

// channelToSensor resolves settings labels (and legacy primary data channels).
var channelToSensor = map[string]wire.SensorID{
	"airspeed":     wire.SensorAirspeed,
	"static":       wire.SensorStatic,
	"mag":          wire.SensorMag,
	ChAirspeedDP:   wire.SensorAirspeed,
	ChStaticP:      wire.SensorStatic,
	ChMagX:         wire.SensorMag,
}

// outboundCmd is queued for the send loop; PrevHz supports Ack rollback.
type outboundCmd struct {
	Cmd      wire.Cmd
	Sensor   wire.SensorID
	PrevHz   uint16
	HasPrev  bool
}

// reader implements sensors.Reader for the pod virtual device. It is
// registered with sensors.Registry purely so the web UI can read and
// write per-sensor attributes; data flow goes directly hub-side from
// Client.onBatch and does NOT pass through sensors.runOne.
//
// SetChannelAttr is fire-and-forget: it enqueues a Cmd on cmdOut and
// optimistically updates the local cache; the next Hello/Status frame
// from the pod reconciles authoritative state.
type reader struct {
	mu     sync.RWMutex
	values map[string]float64       // channel -> latest sample value
	rates  map[wire.SensorID]uint16 // sensor -> last known sampling Hz
	caps   map[wire.SensorID]wire.SensorCap
	out    chan<- outboundCmd
}

func newReader(out chan<- outboundCmd) *reader {
	r := &reader{
		values: make(map[string]float64, len(podChannels)),
		rates:  make(map[wire.SensorID]uint16, 3),
		caps:   make(map[wire.SensorID]wire.SensorCap, 3),
		out:    out,
	}
	return r
}

// applyReading updates the sticky cache with one Reading's values.
func (r *reader) applyReading(rd wire.Reading) {
	r.mu.Lock()
	defer r.mu.Unlock()
	switch v := rd.(type) {
	case wire.AirspeedReading:
		r.values[ChAirspeedDP] = float64(v.DpPa)
		r.values[ChAirspeedTemp] = float64(v.TempC)
	case wire.StaticReading:
		r.values[ChStaticP] = float64(v.PPa)
		r.values[ChStaticTemp] = float64(v.TempC)
	case wire.MagReading:
		r.values[ChMagX] = float64(v.XUt)
		r.values[ChMagY] = float64(v.YUt)
		r.values[ChMagZ] = float64(v.ZUt)
	}
}

// snapshotValues returns a copy of the sticky cache for publishing.
func (r *reader) snapshotValues() map[string]float64 {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make(map[string]float64, len(r.values))
	for k, v := range r.values {
		out[k] = v
	}
	return out
}

// applyHello captures advertised capabilities and seeds the rate cache
// from each sensor's default rate.
func (r *reader) applyHello(h wire.Hello) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, c := range h.Caps.Sensors {
		r.caps[c.ID] = c
		if _, ok := r.rates[c.ID]; !ok {
			r.rates[c.ID] = c.DefaultHz
		}
	}
}

// ---- sensors.Reader interface ----

func (r *reader) Name() string       { return DeviceName }
func (r *reader) Channels() []string { return append([]string(nil), podChannels...) }

func (r *reader) ReadFloat(ch string) (float64, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	v, ok := r.values[ch]
	if !ok {
		return 0, fmt.Errorf("pod: channel %q has no value yet", ch)
	}
	return v, nil
}

// SettingsAttrRecords is the attr snapshot for the registry / web UI: one
// sampling_frequency row per sensor advertised in the last Hello.
func (r *reader) SettingsAttrRecords() []store.AttrRecord {
	r.mu.RLock()
	defer r.mu.RUnlock()
	order := []wire.SensorID{wire.SensorStatic, wire.SensorMag, wire.SensorAirspeed}
	out := make([]store.AttrRecord, 0, len(order))
	for _, sid := range order {
		cap, ok := r.caps[sid]
		if !ok {
			continue
		}
		hz := r.rates[sid]
		if hz == 0 {
			hz = cap.DefaultHz
		}
		out = append(out, store.AttrRecord{
			Channel: sensorSettingsChannel[sid],
			Attr:    "sampling_frequency",
			Value:   strconv.FormatUint(uint64(hz), 10),
		})
	}
	return out
}

// ChannelAttr exposes per-sensor settings (not on mag_y, static_temp, etc.).
func (r *reader) ChannelAttr(ch, attr string) (string, error) {
	sid, ok := channelToSensor[ch]
	if !ok {
		return "", fmt.Errorf("pod: channel %q has no attrs", ch)
	}
	switch attr {
	case "sampling_frequency":
		r.mu.RLock()
		hz, hasHz := r.rates[sid]
		r.mu.RUnlock()
		if !hasHz {
			return "", fmt.Errorf("pod: no rate cached yet for %s", sid)
		}
		return strconv.FormatUint(uint64(hz), 10), nil
	case "sampling_frequency_available":
		r.mu.RLock()
		c, ok := r.caps[sid]
		r.mu.RUnlock()
		if !ok {
			return "", fmt.Errorf("pod: no caps cached yet for %s", sid)
		}
		return fmt.Sprintf("[%d 1 %d]", c.MinHz, c.MaxHz), nil
	default:
		return "", fmt.Errorf("pod: attr %q not supported", attr)
	}
}

func (r *reader) Attr(name string) (string, error) {
	return "", fmt.Errorf("pod: device-level attr %q not supported", name)
}

func (r *reader) SetChannelAttr(ch, attr, value string) error {
	sid, ok := channelToSensor[ch]
	if !ok {
		return fmt.Errorf("pod: channel %q is not writable", ch)
	}
	if attr != "sampling_frequency" {
		return fmt.Errorf("pod: attr %q is not writable", attr)
	}
	hz, err := strconv.ParseUint(value, 10, 16)
	if err != nil {
		return fmt.Errorf("pod: parse sampling_frequency %q: %w", value, err)
	}
	r.mu.Lock()
	if c, ok := r.caps[sid]; ok {
		if uint16(hz) < c.MinHz || uint16(hz) > c.MaxHz {
			r.mu.Unlock()
			return fmt.Errorf("pod: %s rate %d out of range [%d, %d]", sid, hz, c.MinHz, c.MaxHz)
		}
	}
	prevHz := r.rates[sid]
	r.rates[sid] = uint16(hz)
	r.mu.Unlock()
	select {
	case r.out <- outboundCmd{
		Cmd:     wire.CmdSetRate{Sensor: sid, Hz: uint16(hz)},
		Sensor:  sid,
		PrevHz:  prevHz,
		HasPrev: true,
	}:
	default:
		r.mu.Lock()
		r.rates[sid] = prevHz
		r.mu.Unlock()
		return fmt.Errorf("pod: outbound queue full; dropped SetRate")
	}
	return nil
}

// ApplyDeviceConfig loads pod.attrs from config into the rate
// cache and returns SetRate commands to push to the pod (when the link is up).
func (r *reader) ApplyDeviceConfig(dev config.Device) []outboundCmd {
	var outs []outboundCmd
	for k, v := range dev.Attrs {
		ch, attr := sensors.SplitIIOAttr(k)
		if attr != "sampling_frequency" {
			continue
		}
		sid, ok := channelToSensor[ch]
		if !ok {
			continue
		}
		hz64, err := strconv.ParseUint(strings.TrimSpace(v), 10, 16)
		if err != nil {
			continue
		}
		hz := uint16(hz64)
		r.mu.Lock()
		if c, ok := r.caps[sid]; ok {
			if hz < c.MinHz || hz > c.MaxHz {
				r.mu.Unlock()
				continue
			}
		}
		prev := r.rates[sid]
		r.rates[sid] = hz
		r.mu.Unlock()
		outs = append(outs, outboundCmd{
			Cmd:     wire.CmdSetRate{Sensor: sid, Hz: hz},
			Sensor:  sid,
			PrevHz:  prev,
			HasPrev: true,
		})
	}
	return outs
}

// setRateHz updates the cached rate (Ack success or timeout revert).
func (r *reader) setRateHz(sid wire.SensorID, hz uint16) {
	r.mu.Lock()
	r.rates[sid] = hz
	r.mu.Unlock()
}

func (r *reader) ReloadScale() error { return nil }

func (r *reader) WritableAttr(ch, attr string) bool {
	if attr != "sampling_frequency" {
		return false
	}
	sid, ok := channelToSensor[ch]
	if !ok {
		return false
	}
	return sensorSettingsChannel[sid] == ch
}

func (r *reader) Close() error { return nil }
