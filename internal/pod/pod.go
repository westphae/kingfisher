// Package pod is the Pi-side ingest path for the wing-pod telemetry. It
// owns a Transport (UDP today; ESP-NOW dongle later), decodes wire-format
// frames, performs a small EMA time-sync between pod uptime and Pi wall time,
// and republishes each Reading as a live.Sample under the device name "pod".
// The reconstructed TsNs lives on the same wall-clock base as buffered IIO,
// GPS, and derived streams once the Pi clock is GNSS-disciplined. A podReader
// is registered with sensors.Registry purely so the existing /api/devices UI
// can render and write per-sensor settings; the data path does NOT go through
// sensors.runOne.
package pod

import (
	"context"
	"log"
	"sync"
	"sync/atomic"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/location"
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
	st        *store.Store
	registry  *sensors.Registry
	cfg       *config.Holder

	reader *reader

	loggedMu sync.Mutex
	logged   map[string][]store.AttrRecord
	cmdOut   chan outboundCmd

	cmdSeq    atomic.Uint32
	pending   map[uint32]pendingEntry
	pendingMu sync.Mutex

	lastRxNs atomic.Int64

	// offsetNs is (pi_wall_ns at receive) - (pod_uptime_ns of that batch),
	// EMA-smoothed. Set the first time a SampleBatch lands; updated on
	// every subsequent batch. Stored atomically so the AHRS hot path
	// could later read it without locking.
	offsetNs     atomic.Int64
	offsetInited atomic.Bool

	// linkSeq tracks the highest seq we've observed; gaps indicate loss.
	linkSeq   uint32
	rxBatches atomic.Uint64
	rxDropped atomic.Uint64
	txPackets atomic.Uint64

	lastStatusNs  atomic.Int64
	statusRssi    atomic.Int32
	statusBattery atomic.Uint32

	lastBatteryTelemetryNs atomic.Int64
	telemetryBatteryV      atomic.Uint32
	telemetryBatteryI      atomic.Uint32
	telemetryBatteryP      atomic.Uint32
	telemetryBatteryCap    atomic.Uint32
	telemetryBatteryTime   atomic.Uint32
	telemetryBatterySoc    atomic.Uint32
	telemetryBatteryLearned atomic.Uint32

	mu sync.Mutex
}

// New constructs a Client. Caller must call Run to start it. The hub and
// buffer must be the same instances the rest of kingfisher uses so the
// pod's samples land in the cockpit UI and the flight DB.
//
// transport is the link to the pod. Pass nil to skip wiring (useful for
// tests that drive the reader directly).
func New(addr string, transport Transport, hub *live.Hub, buf *store.Buffer, st *store.Store, reg *sensors.Registry, cfg *config.Holder) *Client {
	cmdOut := make(chan outboundCmd, cmdQueueSize)
	c := &Client{
		addr:      addr,
		transport: transport,
		hub:       hub,
		buf:       buf,
		st:        st,
		registry:  reg,
		cfg:       cfg,
		reader:    newReader(cmdOut),
		cmdOut:    cmdOut,
	}
	if reg != nil {
		reg.Register(c.reader, DeviceName, location.Pod)
		for _, name := range DefaultPodDeviceNames() {
			reg.RegisterAlias(name, c.reader, location.Pod)
		}
	}
	if cfg != nil {
		c.reader.SetDesignCapacityFromConfig(cfg.Get().PodBatteryCapacityMah())
		c.applySavedSettings(false)
		c.refreshRegistryViews()
	}
	c.logPodSensorAttrs()
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
	wg.Add(1)
	go func() { defer wg.Done(); c.runPendingExpiry(ctx) }()
	if c.cfg != nil {
		wg.Add(1)
		go func() { defer wg.Done(); c.runConfigReload(ctx) }()
	}

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
		case out := <-c.cmdOut:
			seq := c.cmdSeq.Add(1)
			if out.HasPrev {
				if set, ok := out.Cmd.(wire.CmdSetRate); ok {
					c.trackPending(seq, set.Sensor, out.PrevHz)
				}
			}
			if err := c.transport.Send(wire.CmdFrame{Seq: seq, Cmd: out.Cmd}); err != nil {
				log.Printf("pod: send cmd seq=%d: %v", seq, err)
				if e, ok := c.clearPending(seq); ok {
					c.reader.setRateHz(e.rollbackSensor, e.rollbackHz)
				}
			} else {
				c.noteTxOK()
			}
		}
	}
}

func (c *Client) runPendingExpiry(ctx context.Context) {
	t := time.NewTicker(500 * time.Millisecond)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			c.expirePending()
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
			} else {
				c.noteTxOK()
			}
		}
	}
}

// dispatch routes one decoded frame to its handler.
func (c *Client) dispatch(frame wire.Frame, peer string) {
	c.noteRx()
	switch f := frame.(type) {
	case wire.Hello:
		log.Printf("pod: hello from %s fw=%#x sensors=%d", peer, f.FwVersion, len(f.Caps.Sensors))
		c.reader.applyHello(f)
		c.syncRegistryAliases()
		c.applySavedSettings(true)
		c.pushConfiguredBatteryCapacity()
		c.refreshRegistryViews()
		c.logPodSensorAttrs()
	case wire.Status:
		c.noteStatus(f)
		log.Printf("pod: status uptime=%dus batt=%.2fV rssi=%ddBm tx=%d", f.PodUptimeUs, f.BatteryV, f.RssiDBm, f.TxSeq)
	case wire.SampleBatch:
		c.onBatch(f)
	case wire.Ack:
		if e, ok := c.clearPending(f.ForSeq); ok {
			if !f.OK {
				c.reader.setRateHz(e.rollbackSensor, e.rollbackHz)
				log.Printf("pod: ack for_seq=%d rejected; reverted %s to %d Hz", f.ForSeq, e.rollbackSensor, e.rollbackHz)
				c.refreshRegistryViews()
			} else {
				c.logPodRateAck(e.rollbackSensor)
				c.refreshRegistryViews()
			}
		} else {
			log.Printf("pod: ack for_seq=%d ok=%v (no pending)", f.ForSeq, f.OK)
		}
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
		} else {
			c.noteTxOK()
		}
	case wire.Pong:
		c.maybeRefreshViewsAfterTraffic()
	case wire.CmdFrame:
		// We should never receive Cmd from the pod.
		log.Printf("pod: unexpected Cmd frame from %s", peer)
	}
}

// onBatch handles a SampleBatch: update sticky cache, refresh the pod->Pi wall
// clock offset, and publish one live.Sample per Reading using reconstructed
// measurement time on the shared host wall-clock base.
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
		gap := uint64(b.Seq - c.linkSeq - 1)
		c.rxDropped.Add(gap)
		log.Printf("pod: seq gap %d -> %d (%d dropped)", c.linkSeq, b.Seq, gap)
	}
	c.linkSeq = b.Seq
	c.mu.Unlock()
	c.rxBatches.Add(1)

	capsAdded := false
	var designMah uint16 = config.DefaultPodBatteryCapacityMah
	if c.cfg != nil {
		designMah = c.cfg.Get().PodBatteryCapacityMah()
	}
	for _, rd := range b.Samples {
		if c.reader.ensureCapsFromReading(rd) {
			capsAdded = true
		}
		var dev string
		var values map[string]float64
		var ok bool
		var learned bool
		switch raw := rd.(type) {
		case wire.BatteryReading:
			learned = BatteryGaugeLearned(raw)
			br, _ := NormalizeBatteryReading(raw, designMah)
			dev, values, ok = c.reader.sampleBatteryValues(br, learned)
			rd = br
			if ok {
				c.noteBatteryTelemetry(br, learned)
				c.reader.applyReading(br, learned)
			}
		default:
			rd = NormalizeReading(rd, designMah)
			dev, values, ok = c.reader.sampleDeviceValues(rd)
			if ok {
				c.reader.applyReading(rd, true)
			}
		}
		if !ok {
			continue
		}
		readingNs := podUptimeNs - int64(rd.AgeMicros())*1000 + offset
		sm := live.Sample{
			Device: dev,
			TsNs:   readingNs,
			Values: values,
		}
		c.hub.Publish(sm)
		if c.buf != nil {
			c.buf.Append(sm)
		}
	}
	if capsAdded {
		c.applySavedSettings(false)
		c.refreshRegistryViews()
	}
}

// maybeRefreshViewsAfterTraffic updates the registry when caps were inferred
// from traffic but Hello has not been processed yet.
func (c *Client) maybeRefreshViewsAfterTraffic() {
	if len(c.reader.SettingsAttrRecords()) == 0 {
		return
	}
	c.refreshRegistryViews()
}

func (c *Client) runConfigReload(ctx context.Context) {
	reload := c.cfg.Subscribe()
	for {
		select {
		case <-ctx.Done():
			return
		case <-reload:
			c.reader.SetDesignCapacityFromConfig(c.cfg.Get().PodBatteryCapacityMah())
			c.applySavedSettings(true)
			c.pushConfiguredBatteryCapacity()
			c.refreshRegistryViews()
			c.logPodSensorAttrDiff()
		}
	}
}

// applySavedSettings merges pod.attrs from config.json into the reader.
// When sendCmd is true, enqueues SetRate for each entry (after Hello or reload).
func (c *Client) applySavedSettings(sendCmd bool) {
	if c.cfg == nil {
		return
	}
	dev := c.cfg.Get().PodSettingsDevice()
	outs := c.reader.ApplyDeviceConfig(dev)
	if sendCmd {
		for _, o := range outs {
			c.enqueueOutbound(o)
		}
	}
}

func (c *Client) enqueueOutbound(o outboundCmd) {
	select {
	case c.cmdOut <- o:
	default:
		log.Printf("pod: outbound queue full; dropped %T", o.Cmd)
	}
}

func (c *Client) pushConfiguredBatteryCapacity() {
	if c.cfg == nil {
		return
	}
	o := c.reader.DesignCapacityOutbound(c.cfg.Get().PodBatteryCapacityMah())
	if o != nil {
		c.enqueueOutbound(*o)
	}
}

// RefreshRegistryViews updates registry attr snapshots for pod_* tabs.
func (c *Client) RefreshRegistryViews() { c.refreshRegistryViews() }

func (c *Client) syncRegistryAliases() {
	if c.registry == nil {
		return
	}
	for _, name := range c.reader.TelemetryDeviceNames() {
		c.registry.RegisterAlias(name, c.reader, location.Pod)
	}
}

// refreshRegistryViews resnaps the reader's attrs into the registry so
// the web UI sees the latest caps/rates on each wing sensor tab.
func (c *Client) refreshRegistryViews() {
	if c.registry == nil {
		return
	}
	for _, device := range c.reader.TelemetryDeviceNames() {
		c.registry.Update(device, c.reader.SettingsAttrRecordsForUIDevice(device))
	}
}
