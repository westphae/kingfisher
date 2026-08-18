package pod

import (
	"log"
	"time"

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

const learnedQmaxSaveInterval = 60 * time.Second
const learnedQmaxDeltaMah = 20

func (c *Client) maybePersistLearnedQmax(design uint16, fcc float32) {
	if c.cfg == nil || design < 100 || fcc < 100 {
		return
	}
	qmax := uint16(fcc + 0.5)
	if qmax < 100 {
		return
	}
	lo := float32(design) * 0.50
	hi := float32(design) * 1.20
	if fcc < lo || fcc > hi {
		return
	}

	c.learnedSaveMu.Lock()
	defer c.learnedSaveMu.Unlock()
	now := time.Now()
	if c.lastSavedDesign == design &&
		absDiffU16(c.lastSavedQmax, qmax) < learnedQmaxDeltaMah &&
		!c.lastSavedAt.IsZero() && now.Sub(c.lastSavedAt) < learnedQmaxSaveInterval {
		return
	}
	cur := c.cfg.Get()
	key := config.BatteryMahKey(design)
	if cur.Pod.BatteryLearnedMah != nil && cur.Pod.BatteryLearnedMah[key] == qmax {
		c.lastSavedDesign = design
		c.lastSavedQmax = qmax
		c.lastSavedAt = now
		return
	}

	cp := *cur
	cp.Pod = copyPodLocal(cur.Pod)
	if cp.Pod.BatteryLearnedMah == nil {
		cp.Pod.BatteryLearnedMah = make(map[string]uint16)
	}
	cp.Pod.BatteryLearnedMah[key] = qmax
	c.cfg.Set(&cp)
	if err := config.Save(c.cfg.Path(), &cp); err != nil {
		log.Printf("pod: save learned qmax: %v", err)
		return
	}
	c.lastSavedDesign = design
	c.lastSavedQmax = qmax
	c.lastSavedAt = now
	log.Printf("pod: saved learned capacity %d mAh for %d mAh pack", qmax, design)
}

func absDiffU16(a, b uint16) uint16 {
	if a > b {
		return a - b
	}
	return b - a
}

func copyPodLocal(p config.Pod) config.Pod {
	out := p
	out.BatteryLearnedMah = config.CopyBatteryLearnedMah(p.BatteryLearnedMah)
	if len(p.Attrs) > 0 {
		out.Attrs = make(map[string]string, len(p.Attrs))
		for k, v := range p.Attrs {
			out.Attrs[k] = v
		}
	}
	return out
}
