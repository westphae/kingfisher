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
}

func (c *Client) noteTxOK() {
	c.txPackets.Add(1)
}
