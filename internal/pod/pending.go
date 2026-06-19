package pod

import (
	"log"
	"time"

	"github.com/westphae/kingfisher/internal/pod/wire"
)

const cmdAckTimeout = 2 * time.Second

type pendingEntry struct {
	rollbackSensor wire.SensorID
	rollbackHz     uint16
	rollbackDesign bool // SetAttr design capacity
	sentAt         time.Time
}

// trackPending records a command awaiting Ack (SetRate rollback info).
func (c *Client) trackPending(seq uint32, sensor wire.SensorID, prevHz uint16) {
	c.pendingMu.Lock()
	defer c.pendingMu.Unlock()
	if c.pending == nil {
		c.pending = make(map[uint32]pendingEntry)
	}
	c.pending[seq] = pendingEntry{
		rollbackSensor: sensor,
		rollbackHz:     prevHz,
		sentAt:         time.Now(),
	}
}

func (c *Client) trackPendingDesign(seq uint32) {
	c.pendingMu.Lock()
	defer c.pendingMu.Unlock()
	if c.pending == nil {
		c.pending = make(map[uint32]pendingEntry)
	}
	c.pending[seq] = pendingEntry{rollbackDesign: true, sentAt: time.Now()}
}

func (c *Client) clearPending(seq uint32) (pendingEntry, bool) {
	c.pendingMu.Lock()
	defer c.pendingMu.Unlock()
	e, ok := c.pending[seq]
	if ok {
		delete(c.pending, seq)
	}
	return e, ok
}

func (c *Client) expirePending() {
	now := time.Now()
	c.pendingMu.Lock()
	defer c.pendingMu.Unlock()
	for seq, e := range c.pending {
		if now.Sub(e.sentAt) > cmdAckTimeout {
			delete(c.pending, seq)
			if e.rollbackDesign {
				// Do NOT revert on the command-Ack timeout. The pod's Ack can
				// legitimately land after cmdAckTimeout (wifi RTT ~3s), and the
				// bq27441 reprogram (unseal + CONFIG UPDATE + verify) takes
				// several seconds besides. Reverting here clobbered the user's
				// write before it could take. The 90s chip-confirm path
				// (expireDesignPending + syncDesignCapacityChip on the 0x3C
				// readback) owns the keep-or-revert decision.
				log.Printf("pod: cmd seq=%d ack timed out; awaiting design-capacity chip confirm (0x3C)", seq)
				continue
			}
			c.reader.setRateHz(e.rollbackSensor, e.rollbackHz)
			log.Printf("pod: cmd seq=%d timed out; reverted %s to %d Hz", seq, e.rollbackSensor, e.rollbackHz)
		}
	}
	if mah, ok := c.reader.expireDesignPending(); ok {
		c.revertPodBatteryCapacityConfig(mah)
		c.refreshRegistryViews()
	}
}
