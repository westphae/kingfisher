// Package gps connects to gpsd over TCP and emits live.Samples on the "gps"
// virtual device. Each TPV report becomes one sample. Satellite count comes
// from SKY when gpsd emits it, otherwise from UBX NAV-PVT on a raw side
// channel (u-blox native binary mode often omits SKY).
//
// Timestamp contract: gps.Fix.Time keeps the GNSS fix epoch reported by gpsd,
// but emitted live.Sample TsNs stays on the host wall clock (`time.Now()`) so
// GPS rows share the same disciplined CLOCK_REALTIME time base as buffered IIO,
// pod-reconstructed samples, and derived streams.
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
	"github.com/westphae/kingfisher/internal/units"
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

	lastTPVWall   time.Time
	lastTPVOffset time.Duration
	startupCheck  StartupClockCheck
	offsetHist    offsetTracker
}

func New(addr string, hub *live.Hub, buf *store.Buffer, rateHz func() float64, startup StartupClockCheck) *Client {
	return &Client{addr: addr, hub: hub, buf: buf, rateHz: rateHz, startupCheck: startup}
}

// LastFix returns the most recent fix; HasFix is false if no TPV has been
// seen yet or the latest TPV had Mode < 2.
func (c *Client) LastFix() Fix {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.fix
}

// ClockStatus returns the current Pi-vs-GPS wall-clock assessment used by the
// cockpit header and /api/status. Offset is recv-time minus fix epoch (often
// hundreds of ms from receiver/pipeline lag). Skew is offset minus a running
// median baseline and reflects true wall-clock error once the baseline settles.
func (c *Client) ClockStatus() ClockStatus {
	c.mu.RLock()
	fix := c.fix
	lastTPVWall := c.lastTPVWall
	lastTPVOffset := c.lastTPVOffset
	startup := c.startupCheck
	c.mu.RUnlock()

	baseline, skew, ready := c.offsetHist.baselineAndSkew(lastTPVOffset)
	st := classifyClock(fix.HasFix, fix.Time, lastTPVWall, lastTPVOffset, baseline, skew, ready)
	st.StartupCheck = startup
	return st
}

// gpsdDialTimeout bounds the TCP connect of a single Dial before we give
// up and retry. NOTE: go-gpsd's DialTimeout only times the connect, not the
// blocking banner ReadString that follows on a successful connect — so a
// gpsd that accepts the socket but never sends its banner (wedged daemon)
// can still stall this goroutine, including at shutdown. Acceptable for now:
// the common failure (gpsd fully down) is bounded here, and the wedged case
// only delays graceful shutdown of this one goroutine.
const gpsdDialTimeout = 5 * time.Second

// Run dials gpsd, watches for reports, and republishes them as live.Samples.
// Reconnects with exponential backoff (1 s → 30 s) on error until stop is
// closed; backoff resets after a successful watcher run. Satellite count
// is refreshed periodically from UBX NAV-PVT via brief gpsd polls when SKY
// is absent (u-blox native binary mode).
func (c *Client) Run(stop <-chan struct{}) {
	go c.pollUBXSats(stop)
	backoff := time.Second
	const maxBackoff = 30 * time.Second
	for {
		select {
		case <-stop:
			return
		default:
		}
		ran := c.connectOnce(stop)
		if ran {
			backoff = time.Second
		} else {
			backoff *= 2
			if backoff > maxBackoff {
				backoff = maxBackoff
			}
		}
		select {
		case <-stop:
			return
		case <-time.After(backoff):
		}
	}
}

// minGpsdSession is how long a connection must last to count as "good"
// (reset the backoff). A gpsd that accepts the TCP connect but drops the
// watcher almost immediately (crash-looping / flapping) would otherwise
// reset backoff to 1s every cycle, producing a tight busy reconnect loop.
const minGpsdSession = 3 * time.Second

// connectOnce returns true only if the connection lasted at least
// minGpsdSession (a genuine session); a failed Dial or an immediate
// connect-then-drop returns false so the caller grows the backoff.
func (c *Client) connectOnce(stop <-chan struct{}) bool {
	s, err := gpsd.DialTimeout(c.addr, gpsdDialTimeout)
	if err != nil {
		log.Printf("gps: dial %s: %v", c.addr, err)
		return false
	}
	defer s.Close()
	log.Printf("gps: connected to %s", c.addr)
	start := time.Now()

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
		if n := skySatsInUse(rep); n >= 0 {
			c.sats.Store(int32(n))
		}
	})
	done := s.Watch()
	select {
	case <-done:
		elapsed := time.Since(start)
		log.Printf("gps: watcher returned after %s (gpsd disconnect)", elapsed.Round(time.Millisecond))
		return elapsed >= minGpsdSession
	case <-stop:
		_ = s.Close()
		<-done
		return true
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

// minTrackSpeedMS gates track emission: below ~1 kt the GPS course is noise
// and gpsd usually omits it entirely (decoding as a bogus 0.0).
const minTrackSpeedMS = 0.5

func (c *Client) onTPV(r *gpsd.TPVReport) {
	if c.skipForRate() {
		return
	}
	recvWall := time.Now()
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
	c.lastTPVWall = recvWall
	c.lastTPVOffset = 0
	if !r.Time.IsZero() {
		c.lastTPVOffset = recvWall.Sub(r.Time)
		c.offsetHist.add(c.lastTPVOffset)
	}
	c.mu.Unlock()

	values := map[string]float64{
		"lat":       r.Lat,
		"lon":       r.Lon,
		"alt_msl":   r.Alt,
		"gs":        r.Speed,
		"vs":        r.Climb,
		"h_acc":     r.Eph,
		"v_acc":     r.Epv,
		"gs_acc":    r.Eps,
		"vs_acc":    r.Epc,
		"track_acc": r.Epd,
		"fix":       float64(r.Mode),
		"sats":      float64(c.sats.Load()),
	}
	// gpsd omits TPV track when stationary, which decodes as 0.0 — publishing
	// that would masquerade as a valid (0,360] north track. Track is only
	// meaningful in motion: emit it normalized when moving, NULL otherwise.
	if hasFix && r.Speed > minTrackSpeedMS {
		values["track"] = units.Heading360(r.Track)
	}
	if !r.Time.IsZero() {
		// GNSS fix epoch for display/logging; sample TsNs stays on host wall clock.
		values["fix_time_unix_s"] = float64(r.Time.UnixNano()) / 1e9
	}
	// gpsd sometimes omits fields; drop NaN to avoid polluting SQLite.
	for k, v := range values {
		if math.IsNaN(v) {
			delete(values, k)
		}
	}
	sm := live.Sample{Device: "gps", TsNs: recvWall.UnixNano(), Values: values}
	c.hub.Publish(sm)
	if c.buf != nil {
		c.buf.Append(sm)
	}
}

// skySatsInUse returns satellites used in the navigation solution from a SKY
// report, or -1 if the report carries no satellite list.
func skySatsInUse(rep *gpsd.SKYReport) int {
	if len(rep.Satellites) == 0 {
		return -1
	}
	used := 0
	for _, sat := range rep.Satellites {
		if sat.Used {
			used++
		}
	}
	return used
}

const ubxPollInterval = 3 * time.Second

func (c *Client) pollUBXSats(stop <-chan struct{}) {
	// Seed once at startup so the first TPV isn't stuck at 0.
	if n, ok := pollUBXNumSV(c.addr, 2*time.Second); ok {
		c.sats.Store(int32(n))
	}
	t := time.NewTicker(ubxPollInterval)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			if n, ok := pollUBXNumSV(c.addr, 2*time.Second); ok {
				c.sats.Store(int32(n))
			}
		}
	}
}
