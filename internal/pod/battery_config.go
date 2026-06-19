package pod

import (
	"log"

	"github.com/westphae/kingfisher/internal/config"
)

// DesignCapacityDisplayMah is the design capacity shown in Settings (0x3C when known).
func (c *Client) DesignCapacityDisplayMah() uint16 {
	if c == nil || c.reader == nil {
		return 0
	}
	return c.reader.designCapacityDisplayMah()
}

func (c *Client) revertPodBatteryCapacityConfig(mah uint16) {
	if c.cfg == nil || mah == 0 {
		return
	}
	cur := c.cfg.Get()
	if cur.Pod.BatteryCapacityMah == mah {
		return
	}
	cp := *cur
	cp.Pod = cur.Pod
	cp.Pod.BatteryCapacityMah = mah
	c.cfg.Set(&cp)
	if err := config.Save(c.cfg.Path(), &cp); err != nil {
		log.Printf("pod: save reverted battery_capacity_mah: %v", err)
		return
	}
	c.reader.SetDesignCapacityFromConfig(mah)
}
