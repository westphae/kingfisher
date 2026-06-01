package pod

import (
	"math"
	"time"

	"github.com/westphae/kingfisher/internal/pod/wire"
)

// linkStaleTimeout is how long without inbound pod traffic counts as disconnected.
const linkStaleTimeout = 3 * time.Second

// statusStaleTimeout is how long before the last Status frame is treated as stale.
const statusStaleTimeout = 15 * time.Second

// batteryTelemetryStaleTimeout is how long before the last bq27441 sample is stale.
const batteryTelemetryStaleTimeout = 15 * time.Second

// LinkStats is a snapshot of wing-pod UDP link health for the cockpit UI.
type LinkStats struct {
	Enabled    bool    `json:"enabled"`
	Connected  bool    `json:"connected"`
	RxPackets  uint64  `json:"rx_packets"` // SampleBatch frames received
	RxDropped  uint64  `json:"rx_dropped"` // batches inferred lost from seq gaps
	TxPackets  uint64  `json:"tx_packets"` // Ping/Cmd/Pong frames sent to the pod
	HasRssi    bool    `json:"has_rssi"`
	RssiDBm    int8    `json:"rssi_dbm"`    // pod WiFi STA RSSI toward the Pi AP
	HasBattery bool    `json:"has_battery"` // true when Status reports a non-zero voltage
	BatteryV   float32 `json:"battery_v"`

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
		st.PowerMode = statusPowerModeText(uint8(c.statusPowerMode.Load()))
		st.SleepReason = statusSleepReasonText(uint8(c.statusSleepReason.Load()))
		st.BufferDepth = uint32(c.statusBufferDepth.Load())
		st.DroppedReadings = c.statusDroppedReadings.Load()
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

func (c *Client) noteRx() {
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
}

func statusPowerModeText(v uint8) string {
	switch v {
	case 1:
		return "sleep_pending"
	case 2:
		return "sleeping"
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
