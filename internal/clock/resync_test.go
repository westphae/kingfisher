package clock

import (
	"os"
	"path/filepath"
	"testing"
)

func TestHelperInstalled(t *testing.T) {
	dir := t.TempDir()
	script := filepath.Join(dir, "helper.sh")
	if HelperInstalled(script) {
		t.Fatal("missing helper should be false")
	}
	if err := os.WriteFile(script, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	if !HelperInstalled(script) {
		t.Fatal("installed helper should be true")
	}
	if HelperInstalled("") {
		t.Fatal("empty path should be false")
	}
}
