package web

import (
	"net"
	"testing"

	"github.com/westphae/kingfisher/internal/config"
)

func TestIPTrusted(t *testing.T) {
	entries := []string{"192.168.10.158", "192.168.10.230", "10.0.0.0/8"}
	cases := map[string]bool{
		"192.168.10.158": true,  // exact IP
		"192.168.10.230": true,  // exact IP
		"192.168.10.94":  false, // AP, not listed
		"10.5.6.7":       true,  // inside CIDR
		"11.0.0.1":       false, // outside CIDR
	}
	for ipStr, want := range cases {
		if got := ipTrusted(entries, net.ParseIP(ipStr)); got != want {
			t.Errorf("ipTrusted(%s) = %v, want %v", ipStr, got, want)
		}
	}
}

func TestAccessDecision(t *testing.T) {
	ac := config.Access{
		APSubnet:   "192.168.10.0/24",
		TrustedIPs: []string{"192.168.10.230"},
	}
	cases := map[string]bool{
		"192.168.10.94":  false, // AP stranger → deny
		"192.168.10.230": true,  // AP EFB → allow
		"127.0.0.1":      true,  // loopback (off-AP) → allow
		"192.168.86.50":  true,  // home LAN (off-AP) → allow
		"100.64.0.9":     true,  // Tailscale (off-AP) → allow
	}
	for ipStr, want := range cases {
		if got := accessDecision(ac, net.ParseIP(ipStr)); got != want {
			t.Errorf("accessDecision(%s) = %v, want %v", ipStr, got, want)
		}
	}
	// Nil IP and a broken subnet must fail open (never lock everyone out).
	if !accessDecision(ac, nil) {
		t.Error("nil IP should be allowed")
	}
	if !accessDecision(config.Access{APSubnet: "not-a-cidr"}, net.ParseIP("192.168.10.94")) {
		t.Error("misconfigured subnet should fail open")
	}
}

func TestGatedPath(t *testing.T) {
	gated := []string{"/terminal", "/terminal/x", "/api/terminal/ws", "/api/power/off", "/api/access", "/api/access/add"}
	open := []string{"/", "/api/status", "/api/config", "/static/app.js", "/api/devices"}
	for _, p := range gated {
		if !gatedPath(p) {
			t.Errorf("gatedPath(%q) = false, want true", p)
		}
	}
	for _, p := range open {
		if gatedPath(p) {
			t.Errorf("gatedPath(%q) = true, want false", p)
		}
	}
}

func TestParseARP(t *testing.T) {
	// header + one AP client (complete), one AP entry incomplete (0x0), one
	// off-subnet neighbour, one short/garbage line.
	arp := []byte(`IP address       HW type     Flags       HW address            Mask     Device
192.168.10.94    0x1         0x2         90:e5:b1:7d:9a:64     *        wlan0
192.168.10.55    0x1         0x0         00:00:00:00:00:00     *        wlan0
192.168.86.20    0x1         0x2         aa:bb:cc:dd:ee:ff     *        wlan1
garbage
`)
	got := parseARP(arp, "192.168.10.0/24")
	if len(got) != 1 {
		t.Fatalf("got %d clients, want 1: %+v", len(got), got)
	}
	if got[0].IP != "192.168.10.94" || got[0].MAC != "90:e5:b1:7d:9a:64" {
		t.Errorf("unexpected client: %+v", got[0])
	}
}
