package gdl90

import (
	"log"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

const defaultLeasePoll = 30 * time.Second

// ClientPool maintains UDP connections to EFB clients discovered via DHCP
// leases and/or a static IP list.
type ClientPool struct {
	mu        sync.Mutex
	port      int
	leasePath string
	staticIPs []string
	conns     map[string]*net.UDPConn
}

func NewClientPool(port int, leasePath string, staticIPs []string) *ClientPool {
	if port <= 0 {
		port = 4000
	}
	return &ClientPool{
		port:      port,
		leasePath: leasePath,
		staticIPs: append([]string(nil), staticIPs...),
		conns:     make(map[string]*net.UDPConn),
	}
}

// UpdateConfig refreshes the static IP list (lease path changes require restart).
func (p *ClientPool) UpdateConfig(staticIPs []string) {
	p.mu.Lock()
	p.staticIPs = append([]string(nil), staticIPs...)
	p.mu.Unlock()
}

// Refresh reconciles DHCP leases and static IPs with open UDP sockets.
func (p *ClientPool) Refresh() {
	ips := p.collectIPs()
	p.mu.Lock()
	defer p.mu.Unlock()

	valid := make(map[string]bool, len(ips))
	for _, ip := range ips {
		if ip == "" {
			continue
		}
		valid[ip] = true
		if _, ok := p.conns[ip]; ok {
			continue
		}
		addr, err := net.ResolveUDPAddr("udp", net.JoinHostPort(ip, strconv.Itoa(p.port)))
		if err != nil {
			log.Printf("gdl90: resolve %s: %v", ip, err)
			continue
		}
		conn, err := net.DialUDP("udp", nil, addr)
		if err != nil {
			log.Printf("gdl90: dial %s: %v", addr, err)
			continue
		}
		p.conns[ip] = conn
		log.Printf("gdl90: client connected %s:%d", ip, p.port)
	}
	for ip, conn := range p.conns {
		if !valid[ip] {
			_ = conn.Close()
			delete(p.conns, ip)
			log.Printf("gdl90: client disconnected %s", ip)
		}
	}
}

func (p *ClientPool) collectIPs() []string {
	seen := make(map[string]struct{})
	var out []string
	add := func(ip string) {
		ip = strings.TrimSpace(ip)
		if ip == "" {
			return
		}
		if _, ok := seen[ip]; ok {
			return
		}
		seen[ip] = struct{}{}
		out = append(out, ip)
	}
	for _, ip := range p.staticIPs {
		add(ip)
	}
	if p.leasePath != "" {
		leases, err := parseDHCPLeases(p.leasePath)
		if err != nil {
			if !os.IsNotExist(err) {
				log.Printf("gdl90: dhcp leases %s: %v", p.leasePath, err)
			}
		} else {
			for ip := range leases {
				add(ip)
			}
		}
	}
	return out
}

// parseDHCPLeases reads isc-dhcp-server lease file entries (Stratux-compatible).
func parseDHCPLeases(path string) (map[string]string, error) {
	dat, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	ret := make(map[string]string)
	lines := strings.Split(string(dat), "\n")
	open := false
	blockIP := ""
	for _, line := range lines {
		spaced := strings.Split(line, " ")
		if len(spaced) > 2 && spaced[0] == "lease" {
			open = true
			blockIP = spaced[1]
		} else if open && len(spaced) >= 4 && spaced[2] == "client-hostname" {
			host := strings.TrimRight(strings.TrimLeft(strings.Join(spaced[3:], " "), "\""), "\";")
			ret[blockIP] = host
			open = false
		} else if open && len(spaced) > 0 && strings.HasPrefix(spaced[0], "}") {
			ret[blockIP] = ""
			open = false
		}
	}
	return ret, nil
}

// Send writes a framed GDL90 message to all connected clients.
func (p *ClientPool) Send(msg []byte) int {
	if len(msg) == 0 {
		return 0
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	n := 0
	for ip, conn := range p.conns {
		if _, err := conn.Write(msg); err != nil {
			log.Printf("gdl90: write %s: %v", ip, err)
			continue
		}
		n++
	}
	return n
}

// Count returns the number of connected client sockets.
func (p *ClientPool) Count() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.conns)
}

// Close shuts down all client connections.
func (p *ClientPool) Close() {
	p.mu.Lock()
	defer p.mu.Unlock()
	for ip, conn := range p.conns {
		_ = conn.Close()
		delete(p.conns, ip)
	}
}

// RunLeaseMonitor polls DHCP leases until stop is closed.
func (p *ClientPool) RunLeaseMonitor(stop <-chan struct{}) {
	p.Refresh()
	t := time.NewTicker(defaultLeasePoll)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			p.Refresh()
		}
	}
}
