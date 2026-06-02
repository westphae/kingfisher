package pod

import (
	"fmt"
	"log"
	"strconv"
	"strings"
	"sync"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/store"
)

// DeviceName is the legacy aggregate registry key (sticky cache); hidden from UI tabs.
const DeviceName = "pod"

// channel names exposed on the pod device. They land as columns in the
// flight DB (sanitised) and as keys in the WS feed.
const (
	ChAirspeedDP   = "airspeed_dp_pa"
	ChAirspeedTemp = "airspeed_temp_c"
	ChStaticP      = "static_pressure_pa"
	ChStaticTemp   = "static_temp_c"
	ChMagX         = "mag_x_ut"
	ChMagY         = "mag_y_ut"
	ChMagZ         = "mag_z_ut"
	ChBatteryV     = "battery_voltage_v"
	ChBatteryI     = "battery_current_a"
	ChBatteryP     = "battery_power_w"
	ChBatteryCapRm = "battery_capacity_remain_mah"
	ChBatteryCapFull = "battery_capacity_full_mah"
	ChBatterySOC     = "battery_soc_pct"
	ChBatteryTime    = "battery_time_remain_s"
	ChBatteryLearned = "battery_gauge_learned"
)

// AttrDesignCapacityMah is the UI/config key for BQ27441 design capacity (mAh).
const AttrDesignCapacityMah = "design_capacity_mah"

// BatteryDeviceName is the default wing-tab name for the fuel gauge.
const BatteryDeviceName = "bq27441"

const minDesignCapacityMah = 100
const maxDesignCapacityMah = 10000

var podChannels = []string{
	ChAirspeedDP, ChAirspeedTemp,
	ChStaticP, ChStaticTemp,
	ChMagX, ChMagY, ChMagZ,
}

// legacySettingsChannel maps old config/UI channel names to SensorID.
var legacySettingsChannel = map[string]wire.SensorID{
	"airspeed": wire.SensorAirspeed,
	"static":   wire.SensorStatic,
	"mag":      wire.SensorMag,
	"battery":  wire.SensorBattery,
}

// dataChannelToSensor maps telemetry column names to SensorID.
var dataChannelToSensor = map[string]wire.SensorID{
	ChAirspeedDP: wire.SensorAirspeed,
	ChStaticP:    wire.SensorStatic,
	ChMagX:       wire.SensorMag,
	ChBatteryV:   wire.SensorBattery,
}

// defaultSensorCap matches firmware hello.rs limits when Hello has not arrived.
func defaultSensorCap(sid wire.SensorID) (wire.SensorCap, bool) {
	switch sid {
	case wire.SensorStatic:
		return wire.SensorCap{
			ID: sid, MinHz: 1, MaxHz: 50, DefaultHz: 10,
			DeviceName: wire.NewDeviceName(DefaultDeviceName(sid)),
		}, true
	case wire.SensorMag:
		return wire.SensorCap{
			ID: sid, MinHz: 1, MaxHz: 100, DefaultHz: 10,
			DeviceName: wire.NewDeviceName(DefaultDeviceName(sid)),
		}, true
	case wire.SensorAirspeed:
		return wire.SensorCap{
			ID: sid, MinHz: 1, MaxHz: 50, DefaultHz: 10,
			DeviceName: wire.NewDeviceName(DefaultDeviceName(sid)),
		}, true
	case wire.SensorBattery:
		return wire.SensorCap{
			ID: sid, MinHz: 1, MaxHz: 2, DefaultHz: 1,
			DeviceName: wire.NewDeviceName(DefaultDeviceName(sid)),
		}, true
	default:
		return wire.SensorCap{}, false
	}
}

func (r *reader) deviceNameLocked(sid wire.SensorID) string {
	if c, ok := r.caps[sid]; ok {
		if n := c.DeviceName.String(); n != "" {
			return n
		}
	}
	return DefaultDeviceName(sid)
}

func (r *reader) sensorIDForDevice(device string) (wire.SensorID, bool) {
	if sid, ok := legacySettingsChannel[device]; ok {
		return sid, true
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	for sid, c := range r.caps {
		if c.DeviceName.String() == device {
			return sid, true
		}
	}
	for _, sid := range []wire.SensorID{wire.SensorStatic, wire.SensorMag, wire.SensorAirspeed, wire.SensorBattery} {
		if DefaultDeviceName(sid) == device {
			return sid, true
		}
	}
	return 0, false
}

func (r *reader) sensorIDForKey(ch string) (wire.SensorID, bool) {
	if sid, ok := dataChannelToSensor[ch]; ok {
		return sid, true
	}
	return r.sensorIDForDevice(ch)
}

// TelemetryDeviceNames returns chip names currently advertised or defaulted.
func (r *reader) TelemetryDeviceNames() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	seen := make(map[string]struct{}, 3)
	var out []string
	for _, sid := range []wire.SensorID{wire.SensorStatic, wire.SensorMag, wire.SensorAirspeed, wire.SensorBattery} {
		if _, ok := r.caps[sid]; !ok {
			if _, ok := r.rates[sid]; !ok {
				continue
			}
		}
		name := r.deviceNameLocked(sid)
		if name == "" {
			continue
		}
		if _, dup := seen[name]; dup {
			continue
		}
		seen[name] = struct{}{}
		out = append(out, name)
	}
	return out
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
	mu                sync.RWMutex
	values            map[string]float64       // channel -> latest sample value
	rates             map[wire.SensorID]uint16 // sensor -> last known sampling Hz
	caps              map[wire.SensorID]wire.SensorCap
	designCapacityMah uint16
	outboundDesignMah uint16 // last mAh sent to pod via SetAttr (0 = never)
	out               chan<- outboundCmd
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
// learned applies to BatteryReading only (from raw gauge before normalize).
func (r *reader) applyReading(rd wire.Reading, learned bool) {
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
	case wire.BatteryReading:
		r.values[ChBatteryV] = float64(v.VoltageV)
		r.values[ChBatteryI] = float64(v.CurrentA)
		r.values[ChBatteryP] = float64(v.PowerW)
		if learned {
			r.values[ChBatteryLearned] = 1
		} else {
			r.values[ChBatteryLearned] = 0
		}
		r.values[ChBatteryCapRm] = float64(v.CapacityRemainMah)
		r.values[ChBatteryCapFull] = float64(v.CapacityFullMah)
		r.values[ChBatterySOC] = float64(v.SocPct)
		r.values[ChBatteryTime] = float64(v.TimeRemainS)
	}
}

// sampleDeviceValues maps one wire reading to its telemetry device and sparse columns.
func (r *reader) sampleDeviceValues(rd wire.Reading) (device string, values map[string]float64, ok bool) {
	switch v := rd.(type) {
	case wire.StaticReading:
		return r.deviceNameLocked(wire.SensorStatic), map[string]float64{
			ChStaticP:    float64(v.PPa),
			ChStaticTemp: float64(v.TempC),
		}, true
	case wire.MagReading:
		return r.deviceNameLocked(wire.SensorMag), map[string]float64{
			ChMagX: float64(v.XUt),
			ChMagY: float64(v.YUt),
			ChMagZ: float64(v.ZUt),
		}, true
	case wire.AirspeedReading:
		return r.deviceNameLocked(wire.SensorAirspeed), map[string]float64{
			ChAirspeedDP:   float64(v.DpPa),
			ChAirspeedTemp: float64(v.TempC),
		}, true
	case wire.BatteryReading:
		return "", nil, false
	default:
		return "", nil, false
	}
}

// sampleBatteryValues maps a normalized battery reading to hub columns.
func (r *reader) sampleBatteryValues(v wire.BatteryReading, learned bool) (device string, values map[string]float64, ok bool) {
	learnedVal := 0.0
	if learned {
		learnedVal = 1
	}
	values = map[string]float64{
		ChBatteryV:           float64(v.VoltageV),
		ChBatteryI:           float64(v.CurrentA),
		ChBatteryP:           float64(v.PowerW),
		ChBatteryLearned:     learnedVal,
		ChBatteryCapRm:       float64(v.CapacityRemainMah),
		ChBatteryCapFull:     float64(v.CapacityFullMah),
		ChBatterySOC:         float64(v.SocPct),
		ChBatteryTime:        float64(v.TimeRemainS),
	}
	return r.deviceNameLocked(wire.SensorBattery), values, true
}

// batteryDeviceName returns the hub device tab for the fuel gauge.
func (r *reader) batteryDeviceName() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.deviceNameLocked(wire.SensorBattery)
}

// batteryValuesFromStatus builds hub columns when SampleBatch telemetry is stale
// but Status still carries live voltage from the pod.
func (r *reader) batteryValuesFromStatus(voltageV float32) map[string]float64 {
	if voltageV <= 0.01 {
		return nil
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	out := map[string]float64{
		ChBatteryV: float64(voltageV),
	}
	for _, k := range []string{
		ChBatteryI, ChBatteryP, ChBatteryLearned,
		ChBatteryCapRm, ChBatteryCapFull, ChBatterySOC, ChBatteryTime,
	} {
		if v, ok := r.values[k]; ok {
			out[k] = v
		}
	}
	if _, ok := out[ChBatteryLearned]; !ok {
		out[ChBatteryLearned] = 0
	}
	return out
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
		cap := c
		if cap.DeviceName.String() == "" {
			cap.DeviceName = wire.NewDeviceName(DefaultDeviceName(cap.ID))
		}
		r.caps[cap.ID] = cap
		if _, ok := r.rates[cap.ID]; !ok {
			r.rates[cap.ID] = cap.DefaultHz
		}
	}
}

// ensureCapsFromReading seeds default caps when SampleBatch arrives before
// a Hello (pod started before kingfisher; boot Hello was missed).
func (r *reader) ensureCapsFromReading(rd wire.Reading) bool {
	var sid wire.SensorID
	switch rd.(type) {
	case wire.MagReading:
		sid = wire.SensorMag
	case wire.StaticReading:
		sid = wire.SensorStatic
	case wire.AirspeedReading:
		sid = wire.SensorAirspeed
	case wire.BatteryReading:
		sid = wire.SensorBattery
	default:
		return false
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, ok := r.caps[sid]; ok {
		return false
	}
	if c, ok := defaultSensorCap(sid); ok {
		r.caps[sid] = c
		if _, ok := r.rates[sid]; !ok {
			r.rates[sid] = c.DefaultHz
		}
		return true
	}
	return false
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

// capForSettings returns Hello caps when present, else firmware defaults
// when a rate was loaded from config.json (pod offline / before Hello).
func (r *reader) capForSettings(sid wire.SensorID) (wire.SensorCap, bool) {
	if c, ok := r.caps[sid]; ok {
		return c, true
	}
	if _, ok := r.rates[sid]; ok {
		return defaultSensorCap(sid)
	}
	return wire.SensorCap{}, false
}

// SettingsAttrRecords is the attr snapshot for the registry / web UI: one
// sampling_frequency row per sensor from Hello or from saved pod.attrs.
func (r *reader) SettingsAttrRecords() []store.AttrRecord {
	return r.settingsAttrRecords("")
}

// SettingsAttrRecordsForUIDevice implements registry per-tab attr snapshots.
func (r *reader) SettingsAttrRecordsForUIDevice(uiDevice string) []store.AttrRecord {
	if _, ok := r.sensorIDForDevice(uiDevice); !ok {
		return nil
	}
	return r.settingsAttrRecords(uiDevice)
}

func (r *reader) settingsAttrRecords(onlyChannel string) []store.AttrRecord {
	return r.attrRecords(onlyChannel, false)
}

// FlightLogAttrRecordsForUIDevice returns attrs for sensor_attrs persistence.
func (r *reader) FlightLogAttrRecordsForUIDevice(uiDevice string) []store.AttrRecord {
	if _, ok := r.sensorIDForDevice(uiDevice); !ok {
		return nil
	}
	return r.attrRecords(uiDevice, true)
}

func (r *reader) attrRecords(onlyDevice string, includeCapMeta bool) []store.AttrRecord {
	r.mu.RLock()
	defer r.mu.RUnlock()
	order := []wire.SensorID{wire.SensorStatic, wire.SensorMag, wire.SensorAirspeed, wire.SensorBattery}
	out := make([]store.AttrRecord, 0, len(order)*4)
	for _, sid := range order {
		dev := r.deviceNameLocked(sid)
		if onlyDevice != "" && dev != onlyDevice {
			continue
		}
		cap, ok := r.capForSettings(sid)
		if !ok {
			continue
		}
		hz := r.rates[sid]
		if hz == 0 {
			hz = cap.DefaultHz
		}
		out = append(out, store.AttrRecord{
			Channel: "",
			Attr:    "sampling_frequency",
			Value:   strconv.FormatUint(uint64(hz), 10),
		})
		if sid == wire.SensorBattery && r.designCapacityMah > 0 {
			out = append(out, store.AttrRecord{
				Channel: "",
				Attr:    AttrDesignCapacityMah,
				Value:   strconv.FormatUint(uint64(r.designCapacityMah), 10),
			})
		}
		if includeCapMeta {
			out = append(out,
				store.AttrRecord{Channel: "", Attr: "min_hz", Value: strconv.FormatUint(uint64(cap.MinHz), 10)},
				store.AttrRecord{Channel: "", Attr: "max_hz", Value: strconv.FormatUint(uint64(cap.MaxHz), 10)},
				store.AttrRecord{Channel: "", Attr: "default_hz", Value: strconv.FormatUint(uint64(cap.DefaultHz), 10)},
			)
		}
	}
	return out
}

// ChannelAttr exposes per-sensor settings (not on mag_y, static_temp, etc.).
func (r *reader) ChannelAttr(ch, attr string) (string, error) {
	sid, ok := r.sensorIDForKey(ch)
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
	case AttrDesignCapacityMah:
		if sid != wire.SensorBattery {
			return "", fmt.Errorf("pod: attr %q only on battery sensor", attr)
		}
		r.mu.RLock()
		mah := r.designCapacityMah
		r.mu.RUnlock()
		if mah == 0 {
			return "", fmt.Errorf("pod: design capacity not configured yet")
		}
		return strconv.FormatUint(uint64(mah), 10), nil
	case "sampling_frequency_available":
		r.mu.RLock()
		c, ok := r.capForSettings(sid)
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
	switch attr {
	case "sampling_frequency":
		return r.setSamplingFrequency(ch, value)
	case AttrDesignCapacityMah:
		return r.setDesignCapacity(ch, value)
	default:
		return fmt.Errorf("pod: attr %q is not writable", attr)
	}
}

func (r *reader) setSamplingFrequency(ch, value string) error {
	sid, ok := r.sensorIDForKey(ch)
	if !ok {
		return fmt.Errorf("pod: channel %q is not writable", ch)
	}
	hz, err := strconv.ParseUint(value, 10, 16)
	if err != nil {
		return fmt.Errorf("pod: parse sampling_frequency %q: %w", value, err)
	}
	r.mu.Lock()
	if c, ok := r.caps[sid]; ok {
		if uint16(hz) < c.MinHz || uint16(hz) > c.MaxHz {
			r.mu.Unlock()
			err := fmt.Errorf("pod: %s rate %d out of range [%d, %d] (Hello cap; re-link after firmware update if max looks too low)", sid, hz, c.MinHz, c.MaxHz)
			log.Printf("pod: %v", err)
			return err
		}
	}
	prevHz := r.rates[sid]
	newHz := uint16(hz)
	sHz, mHz, aHz, bHz := RatesAfterChange(r.rates, sid, newHz)
	if !SustainableRates(sHz, mHz, aHz, bHz) {
		r.mu.Unlock()
		err := fmt.Errorf("pod: combined rates (static=%d mag=%d airspeed=%d battery=%d Hz) exceed wing I²C budget", sHz, mHz, aHz, bHz)
		log.Printf("pod: %v", err)
		return err
	}
	r.rates[sid] = newHz
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

func (r *reader) setDesignCapacity(ch, value string) error {
	sid, ok := r.sensorIDForKey(ch)
	if !ok || sid != wire.SensorBattery {
		return fmt.Errorf("pod: design capacity only on %s", BatteryDeviceName)
	}
	mah64, err := strconv.ParseUint(strings.TrimSpace(value), 10, 16)
	if err != nil {
		return fmt.Errorf("pod: parse design_capacity_mah %q: %w", value, err)
	}
	mah := uint16(mah64)
	if mah < minDesignCapacityMah || mah > maxDesignCapacityMah {
		return fmt.Errorf("pod: design capacity %d out of range [%d, %d]", mah, minDesignCapacityMah, maxDesignCapacityMah)
	}
	r.mu.Lock()
	prev := r.designCapacityMah
	r.designCapacityMah = mah
	r.mu.Unlock()
	select {
	case r.out <- outboundCmd{
		Cmd: wire.CmdSetAttr{
			Sensor: wire.SensorBattery,
			Key:    wire.AttrDesignCapacity,
			Value:  float32(mah),
		},
	}:
		r.mu.Lock()
		r.outboundDesignMah = mah
		r.mu.Unlock()
	default:
		r.mu.Lock()
		r.designCapacityMah = prev
		r.mu.Unlock()
		return fmt.Errorf("pod: outbound queue full; dropped SetAttr design capacity")
	}
	return nil
}

// ClearOutboundDesignCapacity allows pushConfiguredBatteryCapacity to resend SetAttr.
func (r *reader) ClearOutboundDesignCapacity() {
	r.mu.Lock()
	r.outboundDesignMah = 0
	r.mu.Unlock()
}

// SetDesignCapacityFromConfig updates the cached design capacity shown in the UI.
func (r *reader) SetDesignCapacityFromConfig(mah uint16) {
	if mah == 0 {
		mah = config.DefaultPodBatteryCapacityMah
	}
	r.mu.Lock()
	r.designCapacityMah = mah
	r.mu.Unlock()
}

// DesignCapacityOutbound returns a SetAttr cmd for the configured capacity, or nil if
// that mAh was already sent on the wire this session.
func (r *reader) DesignCapacityOutbound(mah uint16) *outboundCmd {
	if mah == 0 {
		mah = config.DefaultPodBatteryCapacityMah
	}
	r.mu.Lock()
	if r.outboundDesignMah == mah {
		r.mu.Unlock()
		return nil
	}
	r.designCapacityMah = mah
	r.outboundDesignMah = mah
	r.mu.Unlock()
	o := outboundCmd{
		Cmd: wire.CmdSetAttr{
			Sensor: wire.SensorBattery,
			Key:    wire.AttrDesignCapacity,
			Value:  float32(mah),
		},
	}
	return &o
}

// ApplyDeviceConfig loads pod.attrs from config into the rate
// cache and returns SetRate commands to push to the pod (when the link is up).
func (r *reader) ApplyDeviceConfig(dev config.Device) []outboundCmd {
	var outs []outboundCmd
	for k, v := range dev.Attrs {
		device, _, ok := parsePodAttrKey(k)
		if !ok {
			continue
		}
		sid, ok := r.sensorIDForDevice(device)
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
	return r.WritableForDevice("", ch, attr)
}

// WritableForDevice implements per-tab registry writability (device is the UI tab name).
func (r *reader) WritableForDevice(device, ch, attr string) bool {
	switch attr {
	case "sampling_frequency":
		if device != "" {
			sid, ok := r.sensorIDForDevice(device)
			if !ok {
				return false
			}
			return ch == "" || ch == r.deviceNameLocked(sid) || legacySettingsChannel[ch] == sid
		}
		sid, ok := r.sensorIDForKey(ch)
		if !ok {
			return false
		}
		return ch == "" || ch == r.deviceNameLocked(sid) || legacySettingsChannel[ch] == sid
	case AttrDesignCapacityMah:
		if device != "" {
			sid, ok := r.sensorIDForDevice(device)
			return ok && sid == wire.SensorBattery && (ch == "" || ch == device || ch == "battery")
		}
		sid, ok := r.sensorIDForKey(ch)
		return ok && sid == wire.SensorBattery
	default:
		return false
	}
}

func (r *reader) Close() error { return nil }
