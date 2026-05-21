package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPodListenAddr_precedence(t *testing.T) {
	c := &Config{
		Pod: Pod{
			UDPAddr: "192.168.10.1:47808",
		},
		PodUDPAddr: ":9999",
	}
	if got := c.PodListenAddr(); got != "192.168.10.1:47808" {
		t.Fatalf("pod.udp_addr: got %q", got)
	}

	c = &Config{PodUDPAddr: ":47808"}
	migratePod(c)
	if got := c.PodListenAddr(); got != ":47808" {
		t.Fatalf("legacy pod_udp_addr: got %q", got)
	}

	c = &Config{}
	if got := c.PodListenAddr(); got != defaultPodUDPAddr {
		t.Fatalf("default: got %q", got)
	}

	c = &Config{Pod: Pod{UDPAddr: ""}, PodUDPAddr: ""}
	if got := c.PodListenAddr(); got != defaultPodUDPAddr {
		t.Fatalf("empty fields should fall through to default: got %q", got)
	}
}

func TestLoad_migratesPodUDPAddr(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	if err := os.WriteFile(path, []byte(`{"pod_udp_addr":":47808"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	c, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if c.Pod.UDPAddr != ":47808" {
		t.Fatalf("migratePod: Pod.UDPAddr=%q", c.Pod.UDPAddr)
	}
	if c.PodListenAddr() != ":47808" {
		t.Fatalf("PodListenAddr=%q", c.PodListenAddr())
	}
}

func TestDefaults_pod(t *testing.T) {
	c := Defaults()
	if c.Pod.WiFiSSID != "kingfisher" {
		t.Fatalf("WiFiSSID=%q", c.Pod.WiFiSSID)
	}
	if got := c.PodListenAddr(); got != defaultPodUDPAddr {
		t.Fatalf("PodListenAddr=%q", got)
	}
}
