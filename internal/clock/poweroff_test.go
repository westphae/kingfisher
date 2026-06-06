package clock

import (
	"context"
	"strings"
	"testing"
)

func TestPowerOffMissingHelper(t *testing.T) {
	err := PowerOff(context.Background(), "/nonexistent/kingfisher-poweroff.sh")
	if err == nil {
		t.Fatal("expected error for missing helper")
	}
	if !strings.Contains(err.Error(), "not installed") {
		t.Fatalf("unexpected error: %v", err)
	}
}
