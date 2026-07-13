package web

import (
	"encoding/json"
	"log"
	"net"
	"net/http"
	"os"
	"strings"

	"github.com/westphae/kingfisher/internal/config"
)

// Access control for the root-equivalent endpoints. kingfisher binds loopback
// and Caddy fronts it, so the real client IP arrives in X-Real-Client-IP (set
// by Caddy to the TCP peer — it overwrites any client-sent value, so it can't
// be spoofed). The gate restricts only clients on the open AP: an AP client
// passes solely if its IP is in Access.TrustedIPs; everything off the AP
// (loopback, home LAN over wlan1, Tailscale) is always allowed.

// arpTable lists the kernel's resolved neighbours (IP↔MAC). It is
// world-readable, unlike the NM dnsmasq lease dir (0700 root), and needs no
// AP interface name — we filter by the configured AP subnet.
const arpTable = "/proc/net/arp"

// gatedPath reports whether p is one of the root-equivalent paths. Mirrors the
// Caddy matcher we retired: /terminal*, /api/terminal/*, /api/power/*, and the
// access-management API itself.
func gatedPath(p string) bool {
	return strings.HasPrefix(p, "/terminal") ||
		strings.HasPrefix(p, "/api/terminal/") ||
		strings.HasPrefix(p, "/api/power/") ||
		strings.HasPrefix(p, "/api/access")
}

// clientIP returns the real client IP, preferring the Caddy-set header.
func (s *Server) clientIP(r *http.Request) net.IP {
	if h := strings.TrimSpace(r.Header.Get("X-Real-Client-IP")); h != "" {
		if ip := net.ParseIP(h); ip != nil {
			return ip
		}
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	return net.ParseIP(host)
}

// accessAllowed reports whether the request may reach a gated endpoint.
func (s *Server) accessAllowed(r *http.Request) bool {
	return accessDecision(s.cfg.Get().Access, s.clientIP(r))
}

// accessDecision is the pure allow/deny rule: allow anything off the AP subnet
// (loopback, home LAN, Tailscale) and any AP client whose IP is trusted; deny
// the rest. Unknown IP and a misconfigured subnet fail open rather than lock
// everyone out (the only ingress is loopback Caddy anyway).
func accessDecision(ac config.Access, ip net.IP) bool {
	if ip == nil {
		return true
	}
	_, apNet, err := net.ParseCIDR(ac.APSubnetEffective())
	if err != nil {
		return true
	}
	if !apNet.Contains(ip) {
		return true
	}
	return ipTrusted(ac.TrustedIPs, ip)
}

// ipTrusted reports whether ip matches any trusted entry (plain IP or CIDR).
func ipTrusted(entries []string, ip net.IP) bool {
	for _, e := range entries {
		e = strings.TrimSpace(e)
		if e == "" {
			continue
		}
		if strings.Contains(e, "/") {
			if _, n, err := net.ParseCIDR(e); err == nil && n.Contains(ip) {
				return true
			}
			continue
		}
		if p := net.ParseIP(e); p != nil && p.Equal(ip) {
			return true
		}
	}
	return false
}

// gateSensitive wraps the mux, returning 403 for gated paths from AP clients
// that are not trusted. All ingress is loopback Caddy, so this is the sole
// enforcement point for these endpoints.
func (s *Server) gateSensitive(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if gatedPath(r.URL.Path) && !s.accessAllowed(r) {
			http.Error(w, "Forbidden", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// maxNameLen bounds a user-supplied device label.
const maxNameLen = 64

// apClient is one device currently on the AP (from the ARP neighbour table).
type apClient struct {
	MAC     string `json:"mac"`
	IP      string `json:"ip"`
	Name    string `json:"name,omitempty"`
	Trusted bool   `json:"trusted"`
	Self    bool   `json:"self"`
}

// apClients returns the resolved neighbours whose IP falls in the AP subnet.
// /proc/net/arp columns: IP, HWType, Flags, HWAddr, Mask, Device. Flags 0x0
// means an incomplete (unresolved) entry, which we skip.
func apClients(apCIDR string) []apClient {
	b, err := os.ReadFile(arpTable)
	if err != nil {
		return nil
	}
	return parseARP(b, apCIDR)
}

// parseARP filters /proc/net/arp content to neighbours in the AP subnet.
func parseARP(data []byte, apCIDR string) []apClient {
	_, apNet, cerr := net.ParseCIDR(apCIDR)
	if cerr != nil {
		return nil
	}
	var out []apClient
	for i, line := range strings.Split(string(data), "\n") {
		if i == 0 { // header row
			continue
		}
		f := strings.Fields(line)
		if len(f) < 4 || f[2] == "0x0" {
			continue
		}
		ip := net.ParseIP(f[0])
		if ip == nil || !apNet.Contains(ip) {
			continue
		}
		out = append(out, apClient{MAC: f[3], IP: f[0]})
	}
	return out
}

// handleAccess (GET) returns the trusted list and the current AP clients,
// annotated with trusted/self flags for the Settings UI.
func (s *Server) handleAccess(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	s.writeAccessView(w, r)
}

// writeAccessView renders the trusted list + annotated AP clients.
func (s *Server) writeAccessView(w http.ResponseWriter, r *http.Request) {
	ac := s.cfg.Get().Access
	self := s.clientIP(r)
	clients := apClients(ac.APSubnetEffective())
	for i := range clients {
		ip := net.ParseIP(clients[i].IP)
		if ip == nil {
			continue
		}
		clients[i].Trusted = ipTrusted(ac.TrustedIPs, ip)
		clients[i].Self = self != nil && self.Equal(ip)
		clients[i].Name = ac.Names[clients[i].IP]
	}
	writeJSON(w, map[string]any{
		"ap_subnet":   ac.APSubnetEffective(),
		"trusted_ips": ac.TrustedIPs,
		"names":       ac.Names,
		"clients":     clients,
	})
}

// handleAccessMutate (POST /api/access/add|remove) edits the trusted list.
func (s *Server) handleAccessMutate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	add := strings.HasSuffix(r.URL.Path, "/add")
	var body struct {
		IP string `json:"ip"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	ipStr := strings.TrimSpace(body.IP)
	if net.ParseIP(ipStr) == nil {
		http.Error(w, "invalid ip", http.StatusBadRequest)
		return
	}

	cur := s.cfg.Get()
	nc := *cur
	list := make([]string, 0, len(cur.Access.TrustedIPs)+1)
	found := false
	for _, e := range cur.Access.TrustedIPs {
		if strings.TrimSpace(e) == ipStr {
			found = true
			if !add {
				continue // drop it
			}
		}
		list = append(list, e)
	}
	if add && !found {
		list = append(list, ipStr)
	}
	nc.Access.TrustedIPs = list
	s.cfg.Set(&nc)
	if err := config.Save(s.cfg.Path(), &nc); err != nil {
		log.Printf("web: access save: %v", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	verb := "added"
	if !add {
		verb = "removed"
	}
	log.Printf("access: %s trusted AP device %s", verb, ipStr)
	s.writeAccessView(w, r) // return the refreshed view
}

// handleAccessName (POST /api/access/name) sets or clears a device's label,
// keyed by IP and independent of trust. An empty name deletes the entry.
func (s *Server) handleAccessName(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		IP   string `json:"ip"`
		Name string `json:"name"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	ip := net.ParseIP(strings.TrimSpace(body.IP))
	if ip == nil {
		http.Error(w, "invalid ip", http.StatusBadRequest)
		return
	}
	key := ip.String()
	name := strings.TrimSpace(body.Name)
	if len(name) > maxNameLen {
		name = name[:maxNameLen]
	}

	cur := s.cfg.Get()
	nc := *cur
	names := make(map[string]string, len(cur.Access.Names)+1)
	for k, v := range cur.Access.Names {
		names[k] = v
	}
	if name == "" {
		delete(names, key)
	} else {
		names[key] = name
	}
	if len(names) == 0 {
		names = nil
	}
	nc.Access.Names = names
	s.cfg.Set(&nc)
	if err := config.Save(s.cfg.Path(), &nc); err != nil {
		log.Printf("web: access name save: %v", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	log.Printf("access: named %s %q", key, name)
	s.writeAccessView(w, r)
}
