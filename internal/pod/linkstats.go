package pod

import (
	"math"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod/wire"
)

// linkStaleTimeout is how long without inbound pod traffic counts as disconnected.
const linkStaleTimeout = 3 * time.Second

// statusStaleTimeout is how long before the last Status frame is treated as stale.
const statusStaleTimeout = 15 * time.Second

// batteryTelemetryStaleTimeout is how long before the last bq27441 sample is stale.
const batteryTelemetryStaleTimeout = 15 * time.Second

// recentDropWindow is how long after the last observed drop the cockpit chip
// stays yellow. Older drops remain in the counters but stop warning.
const recentDropWindow = time.Minute

// LinkStats is a snapshot of wing-pod UDP link health for the cockpit UI.
type LinkStats struct {
	Enabled   bool   `json:"enabled"`
	Connected bool   `json:"connected"`
	RxPackets uint64 `json:"rx_packets"` // SampleBatch frames received
	RxDropped uint64 `json:"rx_dropped"` // batches inferred lost from seq gaps
	TxPackets uint64 `json:"tx_packets"` // Ping/Cmd/Pong frames sent to the pod
	TsClamped uint64 `json:"ts_clamped"` // readings whose reconstructed TsNs was clamped to recvNs
	// RecentDrops is true when a drop the hub could have received (seq gap,
	// timestamp clamp, or pod buffer overrun growth while connected) happened
	// within recentDropWindow. Drives the cockpit warn state; the cumulative
	// counters above keep the full session history.
	RecentDrops bool    `json:"recent_drops"`
	HasRssi     bool    `json:"has_rssi"`
	RssiDBm     int8    `json:"rssi_dbm"`    // pod WiFi STA RSSI toward the Pi AP
	HasBattery  bool    `json:"has_battery"` // true when Status reports a non-zero voltage
	BatteryV    float32 `json:"battery_v"`
	// BurstQuiet: the pod said it is in burst mode and the current silence is
	// still within the expected collect window — radio off by design, not a
	// link problem. ProtectSleep: the pod announced deep-sleep battery
	// protection; silence is indefinite until charged.
	BurstQuiet   bool `json:"burst_quiet"`
	ProtectSleep bool `json:"protect_sleep"`
	// StatusAgeS is seconds since the last Status frame (-1 before the first).
	StatusAgeS int64 `json:"status_age_s"`

	HasBatteryTelemetry bool    `json:"has_battery_telemetry"`
	BatteryCurrentA     float32 `json:"battery_current_a"`
	BatteryPowerW       float32 `json:"battery_power_w"`
	BatteryCapacityMah  float32 `json:"battery_capacity_remain_mah"`
	BatteryTimeRemainS  float32 `json:"battery_time_remain_s"`
	BatterySocPct       float32 `json:"battery_soc_pct"`
	BatteryGaugeLearned bool    `json:"battery_gauge_learned"`
	PowerMode           string  `json:"power_mode"`
	SleepReason         string  `json:"sleep_reason"`
	BufferDepth         uint32  `json:"buffer_depth"`
	DroppedReadings     uint64  `json:"dropped_readings"`
}

// LinkStats returns session counters and derived connection state.
func (c *Client) LinkStats() LinkStats {
	enabled := c.transport != nil
	last := c.lastRxNs.Load()
	connected := false
	if enabled && last > 0 {
		connected = time.Now().UnixNano()-last < int64(linkStaleTimeout)
	}
	st := LinkStats{
		Enabled:   enabled,
		Connected: connected,
		RxPackets: c.rxBatches.Load(),
		RxDropped: c.rxDropped.Load(),
		TxPackets: c.txPackets.Load(),
		TsClamped: c.tsClamped.Load(),
	}
	if lastDrop := c.lastDropNs.Load(); lastDrop > 0 {
		st.RecentDrops = time.Now().UnixNano()-lastDrop < int64(recentDropWindow)
	}
	lastStatus := c.lastStatusNs.Load()
	if lastStatus > 0 && time.Now().UnixNano()-lastStatus < int64(statusStaleTimeout) {
		rssi := int8(c.statusRssi.Load())
		if rssi != 0 {
			st.HasRssi = true
			st.RssiDBm = rssi
		}
		batt := math.Float32frombits(c.statusBattery.Load())
		if batt > 0.01 {
			st.HasBattery = true
			st.BatteryV = batt
		}
		st.BufferDepth = uint32(c.statusBufferDepth.Load())
		st.DroppedReadings = c.statusDroppedReadings.Load()
	}
	// Power mode is deliberately not staleness-gated: burst and protect are
	// exactly the modes whose point is a long silence after the last Status.
	st.StatusAgeS = -1
	if lastStatus > 0 {
		mode := uint8(c.statusPowerMode.Load())
		st.PowerMode = statusPowerModeText(mode)
		st.SleepReason = statusSleepReasonText(uint8(c.statusSleepReason.Load()))
		quiet := time.Duration(time.Now().UnixNano() - lastStatus)
		st.StatusAgeS = int64(quiet / time.Second)
		switch mode {
		case powerModeBurstCollect, powerModeBurstUplink:
			st.BurstQuiet = quiet < c.burstQuietAllowance()
		case powerModeProtectPending, powerModeProtect:
			st.ProtectSleep = true
		}
	}
	lastBatt := c.lastBatteryTelemetryNs.Load()
	if lastBatt > 0 && time.Now().UnixNano()-lastBatt < int64(batteryTelemetryStaleTimeout) {
		st.HasBatteryTelemetry = true
		st.BatteryV = math.Float32frombits(c.telemetryBatteryV.Load())
		st.BatteryCurrentA = math.Float32frombits(c.telemetryBatteryI.Load())
		st.BatteryPowerW = math.Float32frombits(c.telemetryBatteryP.Load())
		st.BatteryCapacityMah = math.Float32frombits(c.telemetryBatteryCap.Load())
		st.BatteryTimeRemainS = math.Float32frombits(c.telemetryBatteryTime.Load())
		st.BatterySocPct = math.Float32frombits(c.telemetryBatterySoc.Load())
		st.BatteryGaugeLearned = c.telemetryBatteryLearned.Load() > 0
		if st.BatteryV > 0.01 {
			st.HasBattery = true
		}
	}
	return st
}

// Pod power_mode values (firmware power.rs PowerMode). 1/2 are the retired
// Phase-4 quiesce-sleep, still labeled for old firmware.
const (
	powerModeBurstCollect   = 3
	powerModeBurstUplink    = 4
	powerModeProtectPending = 5
	powerModeProtect        = 6
)

// burstQuietAllowance is how long after the last Status a burst-mode pod may
// stay silent before it counts as a real link problem: one collect window
// plus the firmware's 45 s uplink timeout and reconnect slack.
func (c *Client) burstQuietAllowance() time.Duration {
	windowS := config.DefaultPodBurstWindowS
	if c.cfg != nil {
		windowS = c.cfg.Get().PodBurstWindowS()
	}
	return time.Duration(windowS)*time.Second + 90*time.Second
}

func (c *Client) noteRx() {
	// The pod's cumulative DroppedReadings counter is NOT re-baselined after
	// quiet periods: since the burst protocol, anything the pod drops is
	// stored-data loss (the product), receivable or not, and must warn. Only
	// a pod reboot (counter decrease, handled in noteStatus) re-baselines.
	c.lastRxNs.Store(time.Now().UnixNano())
}

func (c *Client) noteStatus(s wire.Status) {
	c.lastStatusNs.Store(time.Now().UnixNano())
	c.statusRssi.Store(int32(s.RssiDBm))
	c.statusBattery.Store(math.Float32bits(s.BatteryV))
	c.statusPowerMode.Store(uint32(s.PowerMode))
	c.statusSleepReason.Store(uint32(s.SleepReason))
	c.statusBufferDepth.Store(uint32(s.BufferDepth))
	c.statusDroppedReadings.Store(uint64(s.DroppedReadings))
	// Warn only when the pod's overrun counter grows while we are connected.
	// First sight after (re)connect — or a pod reboot resetting the counter —
	// just re-baselines: that backlog predates our listening.
	dr := uint64(s.DroppedReadings)
	if !c.podDropBaselined.Load() || dr < c.podDropBase.Load() {
		c.podDropBase.Store(dr)
		c.podDropBaselined.Store(true)
	} else if dr > c.podDropBase.Load() {
		c.podDropBase.Store(dr)
		c.lastDropNs.Store(time.Now().UnixNano())
	}
	c.maybePublishBatteryFromStatus(s.BatteryV)
}

func (c *Client) maybePublishBatteryFromStatus(v float32) {
	if c.hub == nil {
		return
	}
	last := c.lastBatteryTelemetryNs.Load()
	if last > 0 && time.Now().UnixNano()-last < int64(batteryTelemetryStaleTimeout) {
		return
	}
	values := c.reader.batteryValuesFromStatus(v)
	if values == nil {
		return
	}
	sm := live.Sample{
		Device: c.reader.batteryDeviceName(),
		TsNs:   time.Now().UnixNano(),
		Values: values,
	}
	c.hub.Publish(sm)
	if c.buf != nil {
		c.buf.Append(sm)
	}
}

func statusPowerModeText(v uint8) string {
	switch v {
	case 1:
		return "sleep_pending" // legacy firmware only
	case 2:
		return "sleeping" // legacy firmware only
	case powerModeBurstCollect, powerModeBurstUplink:
		return "burst"
	case powerModeProtectPending:
		return "protect_pending"
	case powerModeProtect:
		return "protect"
	default:
		return "active"
	}
}

func statusSleepReasonText(v uint8) string {
	switch v {
	case 1:
		return "soc"
	case 2:
		return "voltage_fallback"
	case 3:
		return "emergency"
	default:
		return "none"
	}
}

func (c *Client) noteBatteryTelemetry(v wire.BatteryReading, learned bool) {
	c.lastBatteryTelemetryNs.Store(time.Now().UnixNano())
	c.telemetryBatteryV.Store(math.Float32bits(v.VoltageV))
	c.telemetryBatteryI.Store(math.Float32bits(v.CurrentA))
	c.telemetryBatteryP.Store(math.Float32bits(v.PowerW))
	if learned {
		c.telemetryBatteryLearned.Store(1)
		c.telemetryBatteryCap.Store(math.Float32bits(v.CapacityRemainMah))
		c.telemetryBatteryTime.Store(math.Float32bits(v.TimeRemainS))
		c.telemetryBatterySoc.Store(math.Float32bits(v.SocPct))
	} else {
		c.telemetryBatteryLearned.Store(0)
		c.telemetryBatteryCap.Store(0)
		c.telemetryBatteryTime.Store(0)
		c.telemetryBatterySoc.Store(0)
	}
}

func (c *Client) noteTxOK() {
	c.txPackets.Add(1)
}
