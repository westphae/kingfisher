// Package gps connects to gpsd over TCP and emits live.Samples on the
// "gps" virtual device. Each TPV report becomes one sample; SKY satellite
// counts piggy-back on the next TPV.
package gps

import (
	"log"
	"math"
	"sync"
	"sync/atomic"
	"time"

	gpsd "github.com/stratoberry/go-gpsd"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/store"
)

// Fix is the latest known position from gpsd. Other packages (AHRS,
// declination) read it under the package mutex.
type Fix struct {
	HasFix    bool
	Time      time.Time
	Lat       float64
	Lon       float64
	AltMSL    float64 // meters
	Speed     float64 // m/s
	Track     float64 // degrees true
	Climb     float64 // m/s
	HAcc      float64 // meters (gpsd eph, horizontal 2D)
	VAcc      float64 // meters (gpsd epv)
	GsAcc     float64 // m/s (gpsd eps)
	VsAcc     float64 // m/s (gpsd epc)
	TrackAcc  float64 // degrees (gpsd epd)
	Mode      int
	Sats      int
	SatsInUse int
}

type Client struct {
	addr string
	hub  *live.Hub
	buf  *store.Buffer
	// rateHz returns the desired output rate in Hz. The receiver runs at a
	// fixed (higher) rate; we decimate TPV reports down to this rate in
	// software so the cockpit UI can switch between e.g. 5 and 10 Hz
	// without reconfiguring a read-only gpsd. nil or <=0 means no decimation.
	rateHz func() float64

	mu       sync.RWMutex
	fix      Fix
	sats     atomic.Int32 // running last-known SKY count
	lastEmit time.Time    // last TPV passed through; only touched in onTPV
}

func New(addr string, hub *live.Hub, buf *store.Buffer, rateHz func() float64) *Client {
	return &Client{addr: addr, hub: hub, buf: buf, rateHz: rateHz}
}

// LastFix returns the most recent fix; HasFix is false if no TPV has been
// seen yet or the latest TPV had Mode < 2.
func (c *Client) LastFix() Fix {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.fix
}

// Run dials gpsd, watches for reports, and republishes them as live.Samples.
// Reconnects every 2s on error until stop is closed.
func (c *Client) Run(stop <-chan struct{}) {
	for {
		select {
		case <-stop:
			return
		default:
		}
		c.connectOnce(stop)
		select {
		case <-stop:
			return
		case <-time.After(2 * time.Second):
		}
	}
}

func (c *Client) connectOnce(stop <-chan struct{}) {
	s, err := gpsd.Dial(c.addr)
	if err != nil {
		log.Printf("gps: dial %s: %v", c.addr, err)
		return
	}
	defer s.Close()
	log.Printf("gps: connected to %s", c.addr)

	s.AddFilter("TPV", func(r any) {
		rep, ok := r.(*gpsd.TPVReport)
		if !ok {
			return
		}
		c.onTPV(rep)
	})
	s.AddFilter("SKY", func(r any) {
		rep, ok := r.(*gpsd.SKYReport)
		if !ok {
			return
		}
		used := 0
		for _, sat := range rep.Satellites {
			if sat.Used {
				used++
			}
		}
		c.sats.Store(int32(used))
	})
	done := s.Watch()
	select {
	case <-done:
		log.Printf("gps: watcher returned (gpsd disconnect)")
	case <-stop:
		_ = s.Close()
		<-done
	}
}

// skipForRate reports whether this TPV should be dropped to honor the
// configured output rate. gpsd filters run on a single watcher goroutine,
// so lastEmit needs no locking. We gate on 90% of the target period so
// timing jitter in the incoming stream doesn't drop an otherwise-due fix.
func (c *Client) skipForRate() bool {
	if c.rateHz == nil {
		return false
	}
	hz := c.rateHz()
	if hz <= 0 {
		return false
	}
	now := time.Now()
	period := time.Duration(float64(time.Second) / hz)
	if !c.lastEmit.IsZero() && now.Sub(c.lastEmit) < period*9/10 {
		return true
	}
	c.lastEmit = now
	return false
}

func (c *Client) onTPV(r *gpsd.TPVReport) {
	if c.skipForRate() {
		return
	}
	hasFix := r.Mode >= gpsd.Mode2D
	c.mu.Lock()
	c.fix = Fix{
		HasFix:    hasFix,
		Time:      r.Time,
		Lat:       r.Lat,
		Lon:       r.Lon,
		AltMSL:    r.Alt,
		Speed:     r.Speed,
		Track:     r.Track,
		Climb:     r.Climb,
		HAcc:      r.Eph,
		VAcc:      r.Epv,
		GsAcc:     r.Eps,
		VsAcc:     r.Epc,
		TrackAcc:  r.Epd,
		Mode:      int(r.Mode),
		SatsInUse: int(c.sats.Load()),
		Sats:      int(c.sats.Load()),
	}
	c.mu.Unlock()

	values := map[string]float64{
		"lat":       r.Lat,
		"lon":       r.Lon,
		"alt_msl":   r.Alt,
		"gs":        r.Speed,
		"track":     r.Track,
		"vs":        r.Climb,
		"h_acc":     r.Eph,
		"v_acc":     r.Epv,
		"gs_acc":    r.Eps,
		"vs_acc":    r.Epc,
		"track_acc": r.Epd,
		"fix":       float64(r.Mode),
		"sats":      float64(c.sats.Load()),
	}
	// gpsd sometimes omits fields; drop NaN to avoid polluting SQLite.
	for k, v := range values {
		if math.IsNaN(v) {
			delete(values, k)
		}
	}
	sm := live.Sample{Device: "gps", TsNs: time.Now().UnixNano(), Values: values}
	c.hub.Publish(sm)
	if c.buf != nil {
		c.buf.Append(sm)
	}
}
