package pod

import (
	"testing"
	"time"
)

func TestDesignCapacityChipSyncAndPending(t *testing.T) {
	out := make(chan outboundCmd, 1)
	r := newReader(out)
	r.designCapacityChip = 850
	r.designCapacityMah = 850

	if r.syncDesignCapacityChip(850) {
		t.Fatal("unexpected confirm when not pending")
	}
	if r.designCapacityDisplayMah() != 850 {
		t.Fatalf("display = %d", r.designCapacityDisplayMah())
	}

	r.beginDesignPending(900)
	if r.designCapacityDisplayMah() != 900 {
		t.Fatalf("pending display = %d", r.designCapacityDisplayMah())
	}
	if !r.syncDesignCapacityChip(900) {
		t.Fatal("expected confirm at 900")
	}
	if r.designCapacityDisplayMah() != 900 {
		t.Fatalf("after confirm display = %d", r.designCapacityDisplayMah())
	}

	r.beginDesignPending(950)
	r.pendingDesignAt = time.Now().Add(-designCapacityConfirmTimeout - time.Second)
	if mah, ok := r.expireDesignPending(); !ok || mah != 900 {
		t.Fatalf("expire revert mah=%d ok=%v", mah, ok)
	}
	if r.designCapacityDisplayMah() != 900 {
		t.Fatalf("after expire display = %d", r.designCapacityDisplayMah())
	}
}

func TestRevertPendingDesign(t *testing.T) {
	out := make(chan outboundCmd, 1)
	r := newReader(out)
	r.designCapacityChip = 850
	r.designCapacityMah = 850
	r.beginDesignPending(900)
	if mah, ok := r.revertPendingDesign(); !ok || mah != 850 {
		t.Fatalf("revert mah=%d ok=%v", mah, ok)
	}
	if r.pendingDesignMah != 0 {
		t.Fatal("pending should be clear")
	}
}
