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
			c.reader.setRateHz(e.rollbackSensor, e.rollbackHz)
			log.Printf("pod: cmd seq=%d timed out; reverted %s to %d Hz", seq, e.rollbackSensor, e.rollbackHz)
		}
	}
}
