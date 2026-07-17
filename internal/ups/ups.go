package ups

import (
	"context"
	"encoding/json"
	"log"
	"math"
	"sync"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/store"
)

// Device is the hub/SQLite device name.
const Device = "ups"

// openRetry spaces re-open attempts for a missing gauge or GPIO line; a HAT
// that is absent (or an unloaded i2c-dev module) is an expected state, not
// an error loop.
const openRetry = 30 * time.Second

// Monitor polls the X1200 fuel gauge and power-loss line, publishes the
// `ups` device, and requests a clean poweroff when decide() says the
// battery is nearly exhausted.
type Monitor struct {
	holder   *config.Holder
	hub      *live.Hub
	buf      *store.Buffer
	st       *store.Store
	shutdown func(powerOff bool)

	// injectable for tests
	openGauge func() (Gauge, error)
	openPLD   func() (PLD, error)

	gauge          Gauge
	pld            PLD
	gaugeNextTryNs int64
	pldNextTryNs   int64
	gaugeWasOK     bool
	pldWasOK       bool
	acWasOK        bool
	haveACState    bool
	wroteVersion   bool

	dec deciderState

	mu           sync.Mutex
	snap         Snapshot
	lastSampleNs int64
}

// New builds a Monitor. hub is required; buf/st may be nil (skip persistence
// / metadata). shutdown is main's requestShutdown closure.
func New(holder *config.Holder, hub *live.Hub, buf *store.Buffer, st *store.Store, shutdown func(powerOff bool)) *Monitor {
	return &Monitor{
		holder:    holder,
		hub:       hub,
		buf:       buf,
		st:        st,
		shutdown:  shutdown,
		openGauge: openGauge,
		openPLD:   openPLD,
	}
}

// Status returns the latest snapshot for /api/status.
func (m *Monitor) Status() Snapshot {
	m.mu.Lock()
	defer m.mu.Unlock()
	s := m.snap
	if m.lastSampleNs > 0 {
		s.SampleAgeS = float64(time.Now().UnixNano()-m.lastSampleNs) / 1e9
	}
	return s
}

// Run polls until ctx or stop is cancelled.
func (m *Monitor) Run(ctx context.Context, stop <-chan struct{}) {
	cfg := m.holder.Get().UPS
	if !cfg.Enabled {
		log.Printf("ups: disabled")
		return
	}
	rate := cfg.RateHzEffective()
	log.Printf("ups: X1200 monitor at %.2f Hz (soc floor %.0f%%, voltage floor %.2fV, ride timer %s)",
		rate, cfg.ShutdownSocEffective(), cfg.ShutdownVoltageEffective(), rideTimerDesc(cfg.ShutdownAfterEffective()))
	ticker := time.NewTicker(hzDur(rate))
	defer ticker.Stop()
	defer m.closeAll()
	reload := m.holder.Subscribe()

	m.poll()
	for {
		select {
		case <-ctx.Done():
			return
		case <-stop:
			return
		case <-reload:
			ticker.Reset(hzDur(m.holder.Get().UPS.RateHzEffective()))
		case <-ticker.C:
			m.poll()
		}
	}
}

func (m *Monitor) poll() {
	now := time.Now()
	nowNs := now.UnixNano()
	cfg := m.holder.Get().UPS

	var (
		r       reading
		lastErr string
	)

	// Gauge: lazy open with backoff, drop the handle on read error so the
	// next cycle re-probes (a transient bus error then self-heals).
	if m.gauge == nil && nowNs >= m.gaugeNextTryNs {
		g, err := m.openGauge()
		if err != nil {
			m.gaugeNextTryNs = nowNs + openRetry.Nanoseconds()
			lastErr = err.Error()
		} else {
			m.gauge = g
			if !m.wroteVersion {
				if v, err := g.Version(); err == nil {
					log.Printf("ups: MAX17040 present (version 0x%04x)", v)
					if m.st != nil {
						_ = m.st.SetMeta("ups_gauge_version", jsonNum(v))
					}
					m.wroteVersion = true
				}
			}
		}
	}
	if m.gauge != nil {
		v, soc, err := m.gauge.ReadVoltageSOC()
		if err != nil {
			lastErr = err.Error()
			m.gauge.Close()
			m.gauge = nil
			m.gaugeNextTryNs = nowNs + openRetry.Nanoseconds()
		} else {
			r.gaugeOK = true
			r.voltageV = v
			r.socPct = soc
		}
	}
	if r.gaugeOK != m.gaugeWasOK {
		if r.gaugeOK {
			log.Printf("ups: fuel gauge online")
		} else {
			log.Printf("ups: fuel gauge unavailable: %s", lastErr)
		}
		m.gaugeWasOK = r.gaugeOK
	}

	// PLD line, same shape.
	if m.pld == nil && nowNs >= m.pldNextTryNs {
		p, err := m.openPLD()
		if err != nil {
			m.pldNextTryNs = nowNs + openRetry.Nanoseconds()
			lastErr = err.Error()
		} else {
			m.pld = p
		}
	}
	if m.pld != nil {
		ac, err := m.pld.ACPresent()
		if err != nil {
			lastErr = err.Error()
			m.pld.Close()
			m.pld = nil
			m.pldNextTryNs = nowNs + openRetry.Nanoseconds()
		} else {
			r.pldOK = true
			r.acOK = ac
		}
	}
	if r.pldOK != m.pldWasOK {
		if r.pldOK {
			log.Printf("ups: power-loss line online (ac=%v)", r.acOK)
		} else {
			log.Printf("ups: power-loss line unavailable: %s", lastErr)
		}
		m.pldWasOK = r.pldOK
	}

	v := decide(&m.dec, nowNs, r, thresholds{
		afterS:    cfg.ShutdownAfterEffective(),
		socFloor:  cfg.ShutdownSocEffective(),
		voltFloor: cfg.ShutdownVoltageEffective(),
	})
	if r.pldOK {
		if m.haveACState && m.acWasOK != r.acOK {
			if r.acOK {
				log.Printf("ups: external power restored")
			} else {
				log.Printf("ups: external power lost — on battery (%.1fV, %.1f%%)", r.voltageV, r.socPct)
			}
		}
		m.acWasOK = r.acOK
		m.haveACState = true
	}

	vals := map[string]float64{
		"gauge_ok": b2f(r.gaugeOK),
		"pld_ok":   b2f(r.pldOK),
	}
	if r.gaugeOK {
		vals["voltage_v"] = r.voltageV
		vals["soc_pct"] = r.socPct
	}
	if r.pldOK {
		vals["ac_ok"] = b2f(r.acOK)
		vals["on_battery_s"] = v.onBatteryS
	}
	if !math.IsNaN(v.timeRemainingS) {
		vals["time_remaining_s"] = v.timeRemainingS
	}
	sm := live.Sample{Device: Device, TsNs: nowNs, Values: vals}
	m.hub.Publish(sm)
	if m.buf != nil {
		m.buf.Append(sm)
	}

	tte := v.timeRemainingS
	if math.IsNaN(tte) {
		tte = -1
	}
	m.mu.Lock()
	m.snap = Snapshot{
		Enabled:          true,
		Present:          r.gaugeOK,
		PLDOk:            r.pldOK,
		VoltageV:         r.voltageV,
		SocPct:           r.socPct,
		ACOk:             r.acOK,
		OnBatteryS:       v.onBatteryS,
		TimeRemainingS:   tte,
		ShutdownAfterS:   cfg.ShutdownAfterEffective(),
		ShutdownSocPct:   cfg.ShutdownSocEffective(),
		ShutdownVoltageV: cfg.ShutdownVoltageEffective(),
		ShutdownReason:   m.dec.reason,
		LastError:        lastErr,
	}
	m.lastSampleNs = nowNs
	m.mu.Unlock()

	if v.shutdown {
		blob, _ := json.Marshal(map[string]any{
			"reason":       v.reason,
			"voltage_v":    r.voltageV,
			"soc_pct":      r.socPct,
			"on_battery_s": v.onBatteryS,
			"ts_utc":       now.UTC().Format(time.RFC3339),
		})
		if m.st != nil {
			_ = m.st.SetMeta("ups_shutdown", string(blob))
		}
		log.Printf("ups: shutting down: %s", blob)
		if m.shutdown != nil {
			m.shutdown(true)
		}
	}
}

func (m *Monitor) closeAll() {
	if m.gauge != nil {
		m.gauge.Close()
		m.gauge = nil
	}
	if m.pld != nil {
		m.pld.Close()
		m.pld = nil
	}
}

func rideTimerDesc(afterS float64) string {
	if afterS <= 0 {
		return "off (run to floor)"
	}
	return time.Duration(afterS * float64(time.Second)).String()
}

func jsonNum(v uint16) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func b2f(b bool) float64 {
	if b {
		return 1
	}
	return 0
}

func hzDur(hz float64) time.Duration {
	if hz <= 0 {
		hz = 1
	}
	return time.Duration(float64(time.Second) / hz)
}
