package sensors

import (
	"slices"
	"testing"
)

func TestOptionsFromBracketRange_podStaticCap25(t *testing.T) {
	opts := optionsFromBracketRange("[1 1 25]")
	want := []string{"1", "2", "5", "10", "15", "20", "25"}
	if !slices.Equal(opts, want) {
		t.Fatalf("got %v want %v", opts, want)
	}
}

func TestOptionsFromBracketRange_wideMag(t *testing.T) {
	opts := optionsFromBracketRange("[1 1 50]")
	if len(opts) < 5 {
		t.Fatalf("expected preset list, got %v", opts)
	}
	if slices.Contains(opts, "17") {
		t.Fatalf("should not enumerate every integer: %v", opts)
	}
}
