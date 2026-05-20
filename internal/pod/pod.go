// Package pod is the Pi-side ingest path for the wing-pod telemetry. It
// owns a Transport (UDP today; ESP-NOW dongle later), decodes wire-format
// frames, performs a small EMA time-sync between pod uptime and Pi wall
// time, and republishes each Reading as a live.Sample under the device
// name "pod". A podReader is registered with sensors.Registry purely so
// the existing /api/devices UI can render and write per-sensor settings;
// the data path does NOT go through sensors.runOne.
package pod

import (
	"context"
	"log"
	"sync"
	"sync/atomic"
	"time"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
	"github.com/westphae/kingfisher/internal/store"
)

const (
	pingInterval = 5 * time.Second
	cmdQueueSize = 16
	// emaShift = 4 → α = 1/16. Heavier on history, light on new samples;
	// at >10 batches/sec we converge in well under a second from cold
	// start while staying robust to single-packet transit jitter.
	emaShift = 4
)

// Client owns the pod ingest loop. One Client per pod (v1 supports one).
type Client struct {
	addr string

	transport Transport
	hub       *live.Hub
	buf       *store.Buffer
	registry  *sensors.Registry

	reader *reader
	cmdOut chan wire.Cmd

	// offsetNs is (pi_wall_ns at receive) - (pod_uptime_ns of that batch),
	// EMA-smoothed. Set the first time a SampleBatch lands; updated on
	// every subsequent batch. Stored atomically so the AHRS hot path
	// could later read it without locking.
	offsetNs     atomic.Int64
	offsetInited atomic.Bool

	// linkSeq tracks the highest seq we've observed; gaps indicate loss.
	linkSeq uint32
	mu      sync.Mutex
}

// New constructs a Client. Caller must call Run to start it. The hub and
// buffer must be the same instances the rest of kingfisher uses so the
// pod's samples land in the cockpit UI and the flight DB.
//
// transport is the link to the pod. Pass nil to skip wiring (useful for
// tests that drive the reader directly).
func New(addr string, transport Transport, hub *live.Hub, buf *store.Buffer, reg *sensors.Registry) *Client {
	cmdOut := make(chan wire.Cmd, cmdQueueSize)
	c := &Client{
		addr:      addr,
		transport: transport,
		hub:       hub,
		buf:       buf,
		registry:  reg,
		reader:    newReader(cmdOut),
		cmdOut:    cmdOut,
	}
	if reg != nil {
		reg.Register(c.reader, DeviceName)
	}
	return c
}

// Reader exposes the pod's sensors.Reader handle for callers that want
// to register it with a custom registry (tests, mainly).
func (c *Client) Reader() sensors.Reader { return c.reader }

// Run blocks until stop is closed. It runs four concurrent loops:
// recv (decodes frames from the transport), send (writes outbound Cmd
// frames), pinger (periodic time-sync probes), and the stop watcher.
func (c *Client) Run(stop <-chan struct{}) {
	if c.transport == nil {
		log.Printf("pod: no transport; idle")
		<-stop
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	var wg sync.WaitGroup
	wg.Add(1)
	go func() { defer wg.Done(); c.runRecv(ctx) }()
	wg.Add(1)
	go func() { defer wg.Done(); c.runSend(ctx) }()
	wg.Add(1)
	go func() { defer wg.Done(); c.runPinger(ctx) }()

	<-stop
	cancel()
	_ = c.transport.Close()
	wg.Wait()
}

func (c *Client) runRecv(ctx context.Context) {
	for {
		frame, peer, err := c.transport.Recv(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			log.Printf("pod: recv: %v", err)
			continue
		}
		c.dispatch(frame, peer)
	}
}

func (c *Client) runSend(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case cmd := <-c.cmdOut:
			if err := c.transport.Send(wire.CmdFrame{Cmd: cmd}); err != nil {
				log.Printf("pod: send cmd: %v", err)
			}
		}
	}
}

func (c *Client) runPinger(ctx context.Context) {
	t := time.NewTicker(pingInterval)
	defer t.Stop()
	var seq uint32
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			seq++
			// The pinger lives on the Pi; its "uptime" is wall-time ns.
			ping := wire.Ping{Seq: seq, SenderUptimeUs: uint64(time.Now().UnixNano() / 1000)}
			if err := c.transport.Send(ping); err != nil {
				// Most common cause is "no peer learned yet"; quiet log.
				log.Printf("pod: ping: %v", err)
			}
		}
	}
}

// dispatch routes one decoded frame to its handler.
func (c *Client) dispatch(frame wire.Frame, peer string) {
	switch f := frame.(type) {
	case wire.Hello:
		log.Printf("pod: hello from %s fw=%#x sensors=%d", peer, f.FwVersion, len(f.Caps.Sensors))
		c.reader.applyHello(f)
		c.refreshRegistryViews()
	case wire.Status:
		log.Printf("pod: status uptime=%dus batt=%.2fV rssi=%ddBm tx=%d", f.PodUptimeUs, f.BatteryV, f.RssiDBm, f.TxSeq)
	case wire.SampleBatch:
		c.onBatch(f)
	case wire.Ack:
		log.Printf("pod: ack for_seq=%d ok=%v", f.ForSeq, f.OK)
	case wire.Ping:
		// Echo back as a Pong. We don't pong-stamp our own uptime
		// because the pod side does the offset math.
		pong := wire.Pong{
			Seq:            f.Seq,
			SenderUptimeUs: uint64(time.Now().UnixNano() / 1000),
			EchoUptimeUs:   f.SenderUptimeUs,
		}
		if err := c.transport.Send(pong); err != nil {
			log.Printf("pod: pong send: %v", err)
		}
	case wire.Pong:
		// Pi-initiated ping returning. v1 doesn't act on RTT — Phase 4
		// will wire this into a tighter offset estimator.
	case wire.CmdFrame:
		// We should never receive Cmd from the pod.
		log.Printf("pod: unexpected Cmd frame from %s", peer)
	}
}

// onBatch handles a SampleBatch: update sticky cache, refresh time-sync
// offset, and publish one live.Sample per Reading.
func (c *Client) onBatch(b wire.SampleBatch) {
	recvNs := time.Now().UnixNano()
	podUptimeNs := int64(b.PodUptimeUs) * 1000
	rawOffset := recvNs - podUptimeNs

	if !c.offsetInited.Load() {
		c.offsetNs.Store(rawOffset)
		c.offsetInited.Store(true)
	} else {
		cur := c.offsetNs.Load()
		// EMA: cur += (raw - cur) >> emaShift  ≈ cur*(1-α) + raw*α with α=1/16.
		c.offsetNs.Store(cur + (rawOffset-cur)>>emaShift)
	}
	offset := c.offsetNs.Load()

	c.mu.Lock()
	if b.Seq > c.linkSeq+1 && c.linkSeq != 0 {
		log.Printf("pod: seq gap %d -> %d", c.linkSeq, b.Seq)
	}
	c.linkSeq = b.Seq
	c.mu.Unlock()

	for _, rd := range b.Samples {
		c.reader.applyReading(rd)
		readingNs := podUptimeNs - int64(rd.AgeMicros())*1000 + offset
		sm := live.Sample{
			Device: DeviceName,
			TsNs:   readingNs,
			Values: c.reader.snapshotValues(),
		}
		c.hub.Publish(sm)
		if c.buf != nil {
			c.buf.Append(sm)
		}
	}
}

// refreshRegistryViews resnaps the reader's attrs into the registry so
// the web UI sees the latest caps/rates. Called after Hello.
func (c *Client) refreshRegistryViews() {
	if c.registry == nil {
		return
	}
	recs := sensors.SnapshotAttrs(c.reader)
	c.registry.Update(DeviceName, recs)
}
