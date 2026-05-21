package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestPodAttrsRoundTripOnDisk(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")

	initial := Defaults()
	initial.Pod.Attrs = map[string]string{
		"in_mag_sampling_frequency": "50",
	}
	if err := Save(path, initial); err != nil {
		t.Fatal(err)
	}

	loaded, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	dev := loaded.PodSettingsDevice()
	if got := dev.Attrs["in_mag_sampling_frequency"]; got != "50" {
		t.Fatalf("attrs: got %q want 50", got)
	}

	// Simulate power-off: new process reads the same file.
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	again := Defaults()
	if err := json.Unmarshal(b, again); err != nil {
		t.Fatal(err)
	}
	migratePod(again)
	if got := again.Pod.Attrs["in_mag_sampling_frequency"]; got != "50" {
		t.Fatalf("after reboot read: got %q want 50", got)
	}
}

func TestMigratePodAttrsFromDevices(t *testing.T) {
	c := &Config{
		Devices: map[string]Device{
			PodDeviceName: {
				Attrs: map[string]string{
					"in_static_sampling_frequency": "25",
				},
			},
		},
	}
	migratePodAttrs(c)
	if got := c.Pod.Attrs["in_static_sampling_frequency"]; got != "25" {
		t.Fatalf("migrate: got %q", got)
	}
}
