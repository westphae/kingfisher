package gdl90

import (
	"context"
	"log"
	"math"
	"sync"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
)

const maxDataAge = 2 * time.Second

// Stats is a snapshot of broadcaster health for /api/status.
type Stats struct {
	Enabled   bool      `json:"enabled"`
	Clients   int       `json:"clients"`
	LastSend  time.Time `json:"last_send,omitempty"`
	MsgsSent  uint64    `json:"msgs_sent"`
}

// Broadcaster emits GDL90 to connected EFB clients.
type Broadcaster struct {
	mu       sync.RWMutex
	stats    Stats
	pool     *ClientPool
	holder   *config.Holder
	hub      *live.Hub
	gpsc     *gps.Client
}

// NewBroadcaster wires a broadcaster; caller must invoke Run.
func NewBroadcaster(holder *config.Holder, hub *live.Hub, gpsc *gps.Client) *Broadcaster {
	cfg := holder.Get().GDL90
	pool := NewClientPool(cfg.PortEffective(), cfg.DHCPLeasesEffective(), cfg.StaticIPs)
	return &Broadcaster{
		stats:  Stats{Enabled: cfg.Enabled},
		pool:   pool,
		holder: holder,
		hub:    hub,
		gpsc:   gpsc,
	}
}

// Stats returns the latest broadcaster statistics.
func (b *Broadcaster) Stats() Stats {
	b.mu.RLock()
	defer b.mu.RUnlock()
	s := b.stats
	s.Clients = b.pool.Count()
	return s
}

func (b *Broadcaster) noteSend(n int) {
	if n == 0 {
		return
	}
	b.mu.Lock()
	b.stats.LastSend = time.Now()
	b.stats.MsgsSent += uint64(n)
	b.mu.Unlock()
}

func (b *Broadcaster) send(msg []byte) {
	if msg == nil {
		return
	}
	b.noteSend(b.pool.Send(msg))
}

func (b *Broadcaster) sendAll(msgs ...[]byte) {
	for _, m := range msgs {
		b.send(m)
	}
}

// Run broadcasts GDL90 until ctx or stop is cancelled.
func (b *Broadcaster) Run(ctx context.Context, stop <-chan struct{}) {
	defer b.pool.Close()

	go b.pool.RunLeaseMonitor(stop)

	reload := b.holder.Subscribe()
	cfg := b.holder.Get().GDL90

	heartbeatDur := durationHz(cfg.HeartbeatHzEffective())
	ownshipDur := durationHz(cfg.OwnshipHzEffective())
	ahrsDur := durationHz(cfg.AHRSHzEffective())
	ffDur := durationHz(cfg.FFAHRSHzEffective())

	heartbeatTick := time.NewTicker(heartbeatDur)
	ownshipTick := time.NewTicker(ownshipDur)
	ahrsTick := time.NewTicker(ahrsDur)
	ffTick := time.NewTicker(ffDur)
	defer heartbeatTick.Stop()
	defer ownshipTick.Stop()
	defer ahrsTick.Stop()
	defer ffTick.Stop()

	log.Printf("gdl90: broadcasting on UDP :%d (heartbeat %.1f Hz, ownship %.1f Hz, ahrs %.1f Hz)",
		cfg.PortEffective(), 1/heartbeatDur.Seconds(), 1/ownshipDur.Seconds(), 1/ahrsDur.Seconds())

	for {
		select {
		case <-ctx.Done():
			return
		case <-stop:
			return
		case <-reload:
			c := b.holder.Get().GDL90
			b.pool.UpdateConfig(c.StaticIPs)
			heartbeatTick.Reset(durationHz(c.HeartbeatHzEffective()))
			ownshipTick.Reset(durationHz(c.OwnshipHzEffective()))
			ahrsTick.Reset(durationHz(c.AHRSHzEffective()))
			ffTick.Reset(durationHz(c.FFAHRSHzEffective()))
		case <-heartbeatTick.C:
			sit := b.gatherSituation()
			b.sendAll(
				Heartbeat(sit.GPSValid),
				StratuxHeartbeat(sit.GPSValid, sit.AHRSValid),
				ForeFlightID(sit.DeviceShort, sit.DeviceLong),
			)
		case <-ownshipTick.C:
			sit := b.gatherSituation()
			b.sendAll(Ownship(sit), OwnshipGeoAlt(sit))
		case <-ahrsTick.C:
			b.send(AHRSReport(b.gatherSituation()))
		case <-ffTick.C:
			b.send(ForeFlightAHRS(b.gatherSituation()))
		}
	}
}

func durationHz(hz float64) time.Duration {
	if hz <= 0 {
		hz = 1
	}
	return time.Duration(float64(time.Second) / hz)
}

func (b *Broadcaster) gatherSituation() Situation {
	cfg := b.holder.Get()
	gdl := cfg.GDL90
	nowNs := time.Now().UnixNano()

	sit := Situation{
		Callsign:     cfg.Aircraft,
		OwnshipModeS: gdl.OwnshipModeSEffective(),
		DeviceShort:  gdl.DeviceShortNameEffective(),
		DeviceLong:   gdl.DeviceLongNameEffective(),
	}

	snap := b.hub.SnapshotNow()
	if b.gpsc != nil {
		fix := b.gpsc.LastFix()
		if fix.HasFix {
			sit.Lat = fix.Lat
			sit.Lon = fix.Lon
			sit.AltMSLM = fix.AltMSL
			sit.GroundSpeedMps = fix.Speed
			sit.TrackDeg = fix.Track
			sit.ClimbMs = fix.Climb
			sit.GPSNACp = nacpFromHAcc(fix.HAcc)
			if sm, ok := snap.Devices["gps"]; ok && ageNs(nowNs, sm.TsNs) <= maxDataAge {
				sit.GPSValid = true
			} else if !fix.Time.IsZero() && time.Since(fix.Time) < maxDataAge {
				sit.GPSValid = true
			}
		}
	}

	if sm, ok := snap.Devices["ahrs"]; ok && ageNs(nowNs, sm.TsNs) <= maxDataAge {
		sit.RollDeg = sm.Values["roll"]
		sit.PitchDeg = sm.Values["pitch"]
		sit.HeadingDeg = sm.Values["yaw"]
		sit.SlipSkidDeg = sm.Values["slip_skid"]
		sit.TurnRateDegS = sm.Values["turn_rate_deg_s"]
		sit.GLoad = sm.Values["g_load"]
		if !math.IsNaN(sit.RollDeg) && !math.IsNaN(sit.PitchDeg) {
			sit.AHRSValid = true
		}
	}

	if sm, ok := snap.Devices["press_alt"]; ok && ageNs(nowNs, sm.TsNs) <= maxDataAge {
		if pa, ok := sm.Values["pressure_alt_ft"]; ok && !math.IsNaN(pa) {
			sit.PressureAltFt = pa
			sit.BaroValid = true
		}
		if vs, ok := sm.Values["vs_ms"]; ok && !math.IsNaN(vs) {
			sit.BaroVSFpm = vs * msToFpmFactor
		}
	}

	if sm, ok := snap.Devices["airspeed"]; ok && ageNs(nowNs, sm.TsNs) <= maxDataAge {
		if ias, ok := sm.Values["ias_kt"]; ok {
			sit.IASKt = ias
		}
		if tas, ok := sm.Values["tas_kt"]; ok {
			sit.TASKt = tas
		}
	}

	return sit
}

func ageNs(nowNs, tsNs int64) time.Duration {
	if tsNs <= 0 {
		return time.Hour
	}
	d := nowNs - tsNs
	if d < 0 {
		return 0
	}
	return time.Duration(d)
}

func nacpFromHAcc(hAcc float64) uint8 {
	if math.IsNaN(hAcc) || hAcc <= 0 {
		return 9
	}
	// Rough mapping from horizontal accuracy (m) to NACp 0–11 per GDL90.
	switch {
	case hAcc <= 3:
		return 11
	case hAcc <= 5:
		return 10
	case hAcc <= 10:
		return 9
	case hAcc <= 30:
		return 8
	case hAcc <= 92:
		return 7
	default:
		return 6
	}
}

// Run starts a broadcaster when enabled in config; returns nil if disabled.
func Run(ctx context.Context, stop <-chan struct{}, holder *config.Holder, hub *live.Hub, gpsc *gps.Client) *Broadcaster {
	if !holder.Get().GDL90.Enabled {
		return nil
	}
	b := NewBroadcaster(holder, hub, gpsc)
	go b.Run(ctx, stop)
	return b
}
