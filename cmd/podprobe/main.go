// podprobe is a throwaway bench-test listener for Phase 1 wing-pod
// telemetry. It opens a UDP socket, decodes incoming frames with the
// shared wire format, prints one line per packet, and emits a stats
// line every 5 s with received/lost counts and inter-arrival p50/p99.
//
// It binds the same UDP port kingfisher's pod ingest does, so the
// two cannot run simultaneously. Phase 2 retires podprobe in favour
// of kingfisher.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"slices"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/pod/wire"
)

func main() {
	cfgPath := flag.String("config", config.DefaultPath(), "path to kingfisher JSON config")
	addr := flag.String("addr", "", "UDP listen address (overrides config pod.udp_addr)")
	quiet := flag.Bool("quiet", false, "suppress per-packet output; only print stats")
	flag.Parse()

	listenAddr := *addr
	if listenAddr == "" {
		cfg, err := config.Load(*cfgPath)
		if err != nil {
			log.Fatalf("config: %v", err)
		}
		listenAddr = cfg.PodListenAddr()
	}

	pc, err := net.ListenPacket("udp", listenAddr)
	if err != nil {
		log.Fatalf("listen %s: %v", listenAddr, err)
	}
	defer pc.Close()
	log.Printf("podprobe: listening on %s", pc.LocalAddr())

	ctx, cancel := context.WithCancel(context.Background())
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sig
		log.Printf("podprobe: shutting down")
		cancel()
		_ = pc.SetReadDeadline(time.Unix(1, 0))
	}()

	st := newStats()
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		t := time.NewTicker(5 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case now := <-t.C:
				st.printLine(now)
			}
		}
	}()

	buf := make([]byte, 1500)
	for {
		n, src, err := pc.ReadFrom(buf)
		if err != nil {
			if ctx.Err() != nil {
				break
			}
			log.Printf("read: %v", err)
			continue
		}
		recvAt := time.Now()
		frame, derr := wire.Decode(buf[:n])
		if derr != nil {
			st.bump("decode_err")
			log.Printf("decode from %s (%d bytes): %v", src, n, derr)
			continue
		}
		st.onPacket(src.String(), recvAt, frame, *quiet)
	}

	cancel()
	wg.Wait()
	st.printLine(time.Now())
}

// --- stats ---------------------------------------------------------------

type peerState struct {
	lastSeq      uint32
	lastSeqValid bool
	lastRecv     time.Time
	lost         uint64
}

type stats struct {
	mu          sync.Mutex
	peers       map[string]*peerState
	rcv         uint64
	lost        uint64
	decodeErr   uint64
	interArrNs  []int64 // ring buffer, see windowSize
	interArrPos int
	since       time.Time
}

const windowSize = 500

func newStats() *stats {
	return &stats{
		peers:      map[string]*peerState{},
		interArrNs: make([]int64, 0, windowSize),
		since:      time.Now(),
	}
}

func (s *stats) onPacket(src string, recvAt time.Time, frame wire.Frame, quiet bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.rcv++

	ps, ok := s.peers[src]
	if !ok {
		ps = &peerState{}
		s.peers[src] = ps
	}

	gap := uint32(0)
	if ps.lastSeqValid {
		if !recvAt.Before(ps.lastRecv) {
			ia := recvAt.Sub(ps.lastRecv).Nanoseconds()
			if len(s.interArrNs) < windowSize {
				s.interArrNs = append(s.interArrNs, ia)
			} else {
				s.interArrNs[s.interArrPos] = ia
				s.interArrPos = (s.interArrPos + 1) % windowSize
			}
		}
	}

	// Inspect the frame to extract seq (for SampleBatch) and a useful
	// per-packet summary.
	summary := ""
	var seq uint32
	switch f := frame.(type) {
	case wire.Hello:
		summary = fmt.Sprintf("Hello fw=%#x proto=%d sensors=%d", f.FwVersion, f.ProtoVersion, len(f.Caps.Sensors))
	case wire.SampleBatch:
		seq = f.Seq
		if ps.lastSeqValid && seq > ps.lastSeq+1 {
			gap = seq - ps.lastSeq - 1
			ps.lost += uint64(gap)
			s.lost += uint64(gap)
		}
		ps.lastSeq = seq
		ps.lastSeqValid = true
		summary = fmt.Sprintf("SampleBatch seq=%d gap=%d uptime=%dus  %s",
			seq, gap, f.PodUptimeUs, summariseSamples(f.Samples))
	case wire.Status:
		summary = fmt.Sprintf("Status uptime=%dus batt=%.2fV rssi=%ddBm tx=%d", f.PodUptimeUs, f.BatteryV, f.RssiDBm, f.TxSeq)
	case wire.Ping:
		summary = fmt.Sprintf("Ping seq=%d", f.Seq)
	case wire.Pong:
		summary = fmt.Sprintf("Pong seq=%d", f.Seq)
	case wire.Ack:
		summary = fmt.Sprintf("Ack for_seq=%d ok=%v", f.ForSeq, f.OK)
	case wire.CmdFrame:
		summary = fmt.Sprintf("Cmd %T", f.Cmd)
	}
	ps.lastRecv = recvAt

	if !quiet {
		fmt.Printf("%s from %s  %s\n", recvAt.Format("2006-01-02 15:04:05.000"), src, summary)
	}
}

func summariseSamples(rs []wire.Reading) string {
	var parts []string
	for _, r := range rs {
		switch v := r.(type) {
		case wire.AirspeedReading:
			parts = append(parts, fmt.Sprintf("air[dp=%.1fPa t=%.1fC]", v.DpPa, v.TempC))
		case wire.StaticReading:
			parts = append(parts, fmt.Sprintf("stat[p=%.0fPa t=%.1fC]", v.PPa, v.TempC))
		case wire.MagReading:
			parts = append(parts, fmt.Sprintf("mag[x=%.1f y=%.1f z=%.1fuT]", v.XUt, v.YUt, v.ZUt))
		}
	}
	return strings.Join(parts, " ")
}

func (s *stats) bump(kind string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if kind == "decode_err" {
		s.decodeErr++
	}
}

func (s *stats) printLine(now time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	p50, p99 := percentiles(s.interArrNs)
	fmt.Printf("[stats @ %s] rcv=%d lost=%d decode_err=%d ia_p50=%s ia_p99=%s\n",
		now.Format("15:04:05"), s.rcv, s.lost, s.decodeErr, fmtDur(p50), fmtDur(p99))
}

func percentiles(samples []int64) (p50, p99 time.Duration) {
	if len(samples) == 0 {
		return 0, 0
	}
	cp := make([]int64, len(samples))
	copy(cp, samples)
	slices.Sort(cp)
	return time.Duration(cp[len(cp)*50/100]), time.Duration(cp[(len(cp)*99)/100])
}

func fmtDur(d time.Duration) string {
	if d == 0 {
		return "n/a"
	}
	return d.Round(100 * time.Microsecond).String()
}
