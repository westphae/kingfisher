package oled

import (
	"context"
	"log"
	"time"

	"github.com/westphae/kingfisher/internal/clock"
	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/health"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
	"github.com/westphae/kingfisher/internal/store"
	"github.com/westphae/kingfisher/internal/ups"
)

const (
	drawHz      = 1
	openRetry   = 30 * time.Second
	shiftPeriod = 10 * time.Minute
)

// Sources are the live inputs the monitor reads each tick.
type Sources struct {
	Holder *config.Holder
	Hub    *live.Hub
	Buf    *store.Buffer
	Store  *store.Store
	GPS    *gps.Client
	Pod    *pod.Client
	UPS    *ups.Monitor
}

// Monitor owns the SSD1306, GPIO button, and 1 Hz redraw loop.
type Monitor struct {
	src Sources

	disp       *Display
	btn        *button
	nextOpen   time.Time
	cycleIdx   int
	alertIdx   int
	cycleAt    time.Time
	invert     bool
	contrast   byte
	loggedOff  bool
	loggedOpen bool
	btnTried   bool
}

func New(src Sources) *Monitor {
	return &Monitor{src: src}
}

// Run draws until ctx or stop is cancelled. No-op when oled.enabled is false.
func Run(ctx context.Context, src Sources, stop <-chan struct{}) {
	New(src).run(ctx, stop)
}

func (m *Monitor) run(ctx context.Context, stop <-chan struct{}) {
	cfg := m.src.Holder.Get().OLED
	if !cfg.Enabled {
		log.Printf("oled: disabled")
		return
	}
	config.MergeOLEDDefaults(&cfg)
	m.invert = cfg.Invert
	m.contrast = cfg.ContrastEffective()
	log.Printf("oled: health display 1 Hz on %s @ 0x%02x", cfg.BusEffective(), cfg.AddrEffective())

	drawTick := time.NewTicker(time.Second / drawHz)
	btnTick := time.NewTicker(buttonPoll)
	defer drawTick.Stop()
	defer btnTick.Stop()
	defer m.closeAll()
	reload := m.src.Holder.Subscribe()
	m.cycleAt = time.Now()

	m.ensureOpen(time.Now())
	m.redraw(time.Now())
	for {
		select {
		case <-ctx.Done():
			return
		case <-stop:
			return
		case <-reload:
			oc := m.src.Holder.Get().OLED
			config.MergeOLEDDefaults(&oc)
			if !oc.Enabled {
				log.Printf("oled: disabled by config")
				m.closeAll()
				return
			}
			m.applyConfig(oc)
		case now := <-btnTick.C:
			m.onButton(now)
		case now := <-drawTick.C:
			m.ensureOpen(now)
			m.advanceCycle(now)
			m.redraw(now)
		}
	}
}

func (m *Monitor) applyConfig(oc config.OLED) {
	if m.invert != oc.Invert && m.disp != nil {
		_ = m.disp.SetInvert(oc.Invert)
	}
	m.invert = oc.Invert
	c := oc.ContrastEffective()
	if c != m.contrast && m.disp != nil {
		_ = m.disp.SetContrast(c)
	}
	m.contrast = c
}

func (m *Monitor) closeAll() {
	if m.disp != nil {
		_ = m.disp.Close()
		m.disp = nil
	}
	if m.btn != nil {
		m.btn.Close()
		m.btn = nil
	}
	m.btnTried = false
}

func (m *Monitor) ensureOpen(now time.Time) {
	oc := m.src.Holder.Get().OLED
	config.MergeOLEDDefaults(&oc)
	if m.disp == nil && !now.Before(m.nextOpen) {
		d, err := Open(oc.BusEffective(), oc.AddrEffective(), oc.ContrastEffective(), oc.Invert, oc.ColumnOff)
		if err != nil {
			if !m.loggedOff {
				log.Printf("oled: display unavailable: %v", err)
				m.loggedOff = true
			}
			m.nextOpen = now.Add(openRetry)
		} else {
			m.disp = d
			m.loggedOff = false
			m.loggedOpen = true
			m.btnTried = false
		}
	}
	if m.disp != nil && m.btn == nil && oc.ButtonGPIO >= 0 && !m.btnTried {
		m.btnTried = true
		b, err := openButton(oc.ButtonChip, oc.ButtonGPIO)
		if err != nil {
			log.Printf("oled: button unavailable: %v", err)
		} else {
			m.btn = b
		}
	}
}

func (m *Monitor) onButton(now time.Time) {
	if m.btn == nil {
		return
	}
	switch m.btn.poll(now) {
	case pressShort:
		m.cycleIdx++
		m.alertIdx++
		m.cycleAt = now
		m.redraw(now)
	case pressLong:
		m.toggleInvert()
		m.redraw(now)
	}
}

func (m *Monitor) toggleInvert() {
	m.invert = !m.invert
	if m.disp != nil {
		_ = m.disp.SetInvert(m.invert)
	}
	c := m.src.Holder.Get()
	c.OLED.Invert = m.invert
	if err := config.Save(m.src.Holder.Path(), c); err != nil {
		log.Printf("oled: persist invert: %v", err)
	}
}

func (m *Monitor) advanceCycle(now time.Time) {
	oc := m.src.Holder.Get().OLED
	if len(oc.Cycle) == 0 {
		return
	}
	if now.Sub(m.cycleAt) >= oc.CycleDuration() {
		m.cycleIdx++
		m.cycleAt = now
	}
}

func (m *Monitor) redraw(now time.Time) {
	if m.disp == nil {
		return
	}
	rep := m.evaluate(now)
	cfg := m.src.Holder.Get()
	cycle := pickCycle(m.src.Hub.SnapshotNow(), cfg.OLED.Cycle, m.cycleIdx)
	shift := 0
	if now.Unix()%(int64(shiftPeriod.Seconds())*2) >= int64(shiftPeriod.Seconds()) {
		shift = 1
	}
	tail := cfg.Aircraft
	v := View{
		Health:    rep,
		AlertIdx:  m.alertIdx,
		CycleText: cycle,
		ClockHHMM: ClockHHMM(now),
		Tail:      tail,
		Shift:     shift,
	}
	if err := m.disp.Draw(Render(v)); err != nil {
		log.Printf("oled: draw: %v", err)
		_ = m.disp.Close()
		m.disp = nil
		m.nextOpen = now.Add(openRetry)
	}
}

func (m *Monitor) evaluate(now time.Time) health.Report {
	cfg := m.src.Holder.Get()
	in := health.GatherIn{
		Now:       now,
		UPSWarnS:  cfg.OLED.UPSWarnSeconds(),
		Hub:       m.src.Hub.SnapshotNow(),
		Cfg:       cfg,
		IIONames:  m.src.Holder.IIODeviceNames(),
		ExpectGPS: m.src.GPS != nil,
		UPS:       ups.Snapshot{Enabled: cfg.UPS.Enabled},
	}
	if m.src.Buf != nil {
		in.Recording = m.src.Buf.RecordingState()
	}
	if m.src.Store != nil {
		if free, err := m.src.Store.VolumeFreeBytes(); err == nil {
			in.DiskFree = &free
		}
	}
	if m.src.GPS != nil {
		in.GPSFix = m.src.GPS.LastFix()
		in.GPSClock = m.src.GPS.ClockStatus()
	}
	in.Clock = clock.QueryDiscipline(context.Background())
	if m.src.Pod != nil {
		in.Pod = m.src.Pod.LinkStats()
		in.PodDevices = m.src.Pod.TelemetryDeviceNames()
	}
	if m.src.UPS != nil {
		in.UPS = m.src.UPS.Status()
	}
	return health.Evaluate(in)
}
