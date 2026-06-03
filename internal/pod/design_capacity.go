package pod

import (
	"log"
	"time"
)

// How long to wait for 0x3C to match a user SetAttr before reverting UI/config.
const designCapacityConfirmTimeout = 90 * time.Second

// designCapacityDisplayMah returns the value shown in Settings (chip-backed).
func (r *reader) designCapacityDisplayMah() uint16 {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.designCapacityDisplayLocked()
}

func (r *reader) designCapacityDisplayLocked() uint16 {
	if r.pendingDesignMah != 0 {
		return r.pendingDesignMah
	}
	if r.designCapacityChip != 0 {
		return r.designCapacityChip
	}
	return r.designCapacityMah
}

// syncDesignCapacityChip updates chip readback (0x3C). Returns true when a pending
// user write was confirmed by the gauge matching target.
func (r *reader) syncDesignCapacityChip(chipMah uint16) (confirmed bool) {
	if chipMah == 0 {
		return false
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.designCapacityChip = chipMah
	if pending := r.pendingDesignMah; pending != 0 {
		if chipMah == pending {
			r.pendingDesignMah = 0
			r.pendingDesignAt = time.Time{}
			r.designCapacityMah = chipMah
			return true
		}
		return false
	}
	r.designCapacityMah = chipMah
	return false
}

// beginDesignPending records a user-initiated design capacity change awaiting chip confirm.
func (r *reader) beginDesignPending(mah uint16) {
	r.mu.Lock()
	defer r.mu.Unlock()
	prev := r.designCapacityChip
	if prev == 0 {
		prev = r.designCapacityMah
	}
	r.prevDesignMah = prev
	r.pendingDesignMah = mah
	r.pendingDesignAt = time.Now()
	r.designCapacityMah = mah
}

// revertPendingDesign clears a pending write and returns the value to show/save (chip or prev).
func (r *reader) revertPendingDesign() (uint16, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.pendingDesignMah == 0 {
		return 0, false
	}
	chip := r.designCapacityChip
	if chip == 0 {
		chip = r.prevDesignMah
	}
	if chip == 0 {
		chip = r.designCapacityMah
	}
	r.designCapacityMah = chip
	r.pendingDesignMah = 0
	r.pendingDesignAt = time.Time{}
	return chip, true
}

// expireDesignPending reverts when chip never matched within the confirm window.
func (r *reader) expireDesignPending() (uint16, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.pendingDesignMah == 0 {
		return 0, false
	}
	if r.pendingDesignAt.IsZero() || time.Since(r.pendingDesignAt) < designCapacityConfirmTimeout {
		return 0, false
	}
	target := r.pendingDesignMah
	chip := r.designCapacityChip
	if chip == target {
		r.pendingDesignMah = 0
		r.pendingDesignAt = time.Time{}
		r.designCapacityMah = chip
		return 0, false
	}
	if chip == 0 {
		chip = r.prevDesignMah
	}
	log.Printf("pod: design capacity %d mAh not programmed; reverting settings to %d mAh (chip 0x3C)", target, chip)
	r.designCapacityMah = chip
	r.pendingDesignMah = 0
	r.pendingDesignAt = time.Time{}
	return chip, true
}
