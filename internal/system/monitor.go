package system

import (
	"context"
	"fmt"
	"log"
	"runtime"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/store"
)

// vcgencmdTimeout bounds each vcgencmd invocation so a wedged call cannot
// stall the poll loop.
const vcgencmdTimeout = 2 * time.Second

// Monitor polls Pi host telemetry and publishes it as the `system` device.
type Monitor struct {
	holder *config.Holder
	hub    *live.Hub
	buf    *store.Buffer
	st     *store.Store
	vc     vcRunner

	hwmon map[string]string

	prevIdle, prevTotal int64
	havePrev            bool

	last map[string]float64 // previous flags, for state-change logging
}

// New builds a Monitor. hub is required; buf/st may be nil (buf omitted skips
// persistence; st omitted skips disk stats and startup metadata).
func New(holder *config.Holder, hub *live.Hub, buf *store.Buffer, st *store.Store) *Monitor {
	return &Monitor{
		holder: holder,
		hub:    hub,
		buf:    buf,
		st:     st,
		vc:     runVcgencmd,
		last:   make(map[string]float64),
	}
}

// Run polls host telemetry until ctx or stop is cancelled. It is a no-op when
// system telemetry is disabled in config.
func Run(ctx context.Context, holder *config.Holder, hub *live.Hub, buf *store.Buffer, st *store.Store, stop <-chan struct{}) {
	New(holder, hub, buf, st).run(ctx, stop)
}

func (m *Monitor) run(ctx context.Context, stop <-chan struct{}) {
	if !m.holder.Get().System.EnabledEffective() {
		log.Printf("system: telemetry disabled")
		return
	}
	m.hwmon = discoverHwmon()
	m.writeConstants()

	rate := m.holder.Get().System.RateHzEffective()
	log.Printf("system: host telemetry at %.2f Hz", rate)
	ticker := time.NewTicker(hzDur(rate))
	defer ticker.Stop()
	reload := m.holder.Subscribe()

	m.collectAndPublish(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-stop:
			return
		case <-reload:
			ticker.Reset(hzDur(m.holder.Get().System.RateHzEffective()))
		case <-ticker.C:
			m.collectAndPublish(ctx)
		}
	}
}

func (m *Monitor) collectAndPublish(ctx context.Context) {
	vals := make(map[string]float64, 40)

	idle, total, ok := collectProc(vals, m.prevIdle, m.prevTotal, m.havePrev)
	m.prevIdle, m.prevTotal = idle, total
	if ok {
		m.havePrev = true
	}

	collectHwmon(vals, m.hwmon)
	m.collectDisk(vals)

	vcCtx, cancel := context.WithTimeout(ctx, vcgencmdTimeout)
	collectVcgencmd(vcCtx, vals, m.vc)
	cancel()

	m.collectSelf(vals)
	m.logStateChanges(vals)

	sm := live.Sample{Device: Device, TsNs: time.Now().UnixNano(), Values: vals}
	m.hub.Publish(sm)
	if m.buf != nil {
		m.buf.Append(sm)
	}
}

func (m *Monitor) collectDisk(vals map[string]float64) {
	if m.st == nil {
		return
	}
	free, total, err := m.st.VolumeStats()
	if err != nil || total <= 0 {
		return
	}
	vals["disk_free_gb"] = float64(free) / 1e9
	vals["disk_used_pct"] = float64(total-free) / float64(total) * 100
}

func (m *Monitor) collectSelf(vals map[string]float64) {
	if s, ok := readTrim("/proc/self/status"); ok {
		if kb, ok := parseSelfRSSkB(s); ok {
			vals["proc_rss_mb"] = float64(kb) / 1024
		}
	}
	vals["goroutines"] = float64(runtime.NumGoroutine())
}

// stateFlags are the boolean channels whose 0↔1 transitions are logged once
// (never per poll), so the journal is not flooded while degraded.
var stateFlags = []string{
	"undervolt_now", "throttled_now", "freq_capped_now", "soft_temp_now",
	"undervolt_since_boot", "throttled_since_boot", "freq_capped_since_boot", "soft_temp_since_boot",
}

func (m *Monitor) logStateChanges(vals map[string]float64) {
	for _, k := range stateFlags {
		cur, ok := vals[k]
		if !ok {
			continue
		}
		prev, had := m.last[k]
		if !had || prev == cur {
			m.last[k] = cur
			continue
		}
		if cur != 0 {
			if v, ok := vals["supply_v"]; ok {
				log.Printf("system: %s asserted (supply %.2fV)", k, v)
			} else {
				log.Printf("system: %s asserted", k)
			}
		} else {
			log.Printf("system: %s cleared", k)
		}
		m.last[k] = cur
	}

	// A shrinking uptime means the Pi rebooted under us (e.g. watchdog).
	if up, ok := vals["uptime_s"]; ok {
		if prev, had := m.last["uptime_s"]; had && up < prev-1 {
			log.Printf("system: uptime reset %.0fs -> %.0fs (reboot detected)", prev, up)
		}
		m.last["uptime_s"] = up
	}
}

// writeConstants records slow-changing host facts to flight-DB metadata once
// at startup, keeping them out of every telemetry row.
func (m *Monitor) writeConstants() {
	if m.st == nil {
		return
	}
	setMeta := func(key, val string) {
		if val != "" {
			_ = m.st.SetMeta(key, val)
		}
	}
	setMeta("system_cpu_count", fmt.Sprintf("%d", runtime.NumCPU()))
	if s, ok := readTrim("/proc/sys/kernel/osrelease"); ok {
		setMeta("system_kernel", s)
	}
	if s, ok := readTrim("/proc/meminfo"); ok {
		if mt := parseMeminfo(s)["MemTotal"]; mt > 0 {
			setMeta("system_mem_total_mb", fmt.Sprintf("%d", mt/1024))
		}
	}
	if s, ok := readTrim("/sys/firmware/devicetree/base/model"); ok {
		// device-tree strings are NUL-terminated.
		setMeta("system_pi_model", trimNul(s))
	}
	if m.st != nil {
		if _, total, err := m.st.VolumeStats(); err == nil && total > 0 {
			setMeta("system_disk_total_gb", fmt.Sprintf("%.1f", float64(total)/1e9))
		}
	}
}

func trimNul(s string) string {
	for i := 0; i < len(s); i++ {
		if s[i] == 0 {
			return s[:i]
		}
	}
	return s
}

func hzDur(hz float64) time.Duration {
	if hz <= 0 {
		hz = 1
	}
	return time.Duration(float64(time.Second) / hz)
}
