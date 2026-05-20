package pod

import (
	"fmt"
	"strconv"
	"sync"

	"github.com/westphae/kingfisher/internal/pod/wire"
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

// sensorPrimaryChannel selects, per sensor, the channel that hosts the
// writable `sampling_frequency` attribute in the UI. The pod side reads
// one rate per sensor; we surface that rate on a single channel rather
// than every channel that sensor produces, to avoid duplicating the
// control surface.
var sensorPrimaryChannel = map[wire.SensorID]string{
	wire.SensorAirspeed: ChAirspeedDP,
	wire.SensorStatic:   ChStaticP,
	wire.SensorMag:      ChMagX,
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
	out    chan<- wire.Cmd
}

func newReader(out chan<- wire.Cmd) *reader {
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

// ChannelAttr exposes only the attrs the UI is meant to render. Everything
// else returns an error so SnapshotAttrs prunes it silently.
func (r *reader) ChannelAttr(ch, attr string) (string, error) {
	sid, ok := sensorForPrimaryChannel(ch)
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
	sid, ok := sensorForPrimaryChannel(ch)
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
	r.rates[sid] = uint16(hz)
	r.mu.Unlock()
	// Fire-and-forget. If the outbound queue is full, drop — the next
	// retry will catch up.
	select {
	case r.out <- wire.CmdSetRate{Sensor: sid, Hz: uint16(hz)}:
	default:
		return fmt.Errorf("pod: outbound queue full; dropped SetRate")
	}
	return nil
}

func (r *reader) ReloadScale() error { return nil }

func (r *reader) WritableAttr(ch, attr string) bool {
	_, ok := sensorForPrimaryChannel(ch)
	return ok && attr == "sampling_frequency"
}

func (r *reader) Close() error { return nil }

// sensorForPrimaryChannel reverses sensorPrimaryChannel.
func sensorForPrimaryChannel(ch string) (wire.SensorID, bool) {
	for sid, primary := range sensorPrimaryChannel {
		if primary == ch {
			return sid, true
		}
	}
	return 0, false
}
