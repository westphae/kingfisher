package sensors

import (
	"context"
	"log"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/westphae/go-iio"
	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/location"
	"github.com/westphae/kingfisher/internal/store"
	"github.com/westphae/kingfisher/internal/units"
)

const (
	defaultBufferLength = 32
	maxBufferLength     = 128
	minBufferLength     = 8
)

func (r *iioReader) bufferChannels() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	var data []string
	var haveTS bool
	for _, c := range r.d.Channels() {
		name := c.Name()
		if name == "timestamp" {
			if _, ok := c.Scan(); ok {
				haveTS = true
			}
			continue
		}
		if _, ok := c.Scan(); ok {
			data = append(data, name)
		}
	}
	if haveTS {
		data = append(data, "timestamp")
	}
	return data
}

func bufferLengthForHz(hz float64) int {
	if hz <= 0 {
		hz = 10
	}
	// Ring depth ~2× one second of samples at the trigger rate.
	n := int(math.Ceil(hz * 2))
	if n < minBufferLength {
		return minBufferLength
	}
	if n > maxBufferLength {
		return maxBufferLength
	}
	return n
}

func triggerName(uiName string) string {
	s := strings.Map(func(r rune) rune {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
			return r
		default:
			return '-'
		}
	}, uiName)
	if s == "" {
		s = "device"
	}
	return "kingfisher-" + s
}

func (r *iioReader) openIIOBuffer(chans []string, blen int, trigger string) (*iio.Buffer, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.d.Buffer(iio.BufferOptions{
		Channels:  chans,
		Length:    blen,
		Watermark: 1,
		Trigger:   trigger,
	})
}

func (r *iioReader) bufferBytesAvailable() (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, err := r.d.Attr("buffer/data_available")
	if err != nil {
		return 0, err
	}
	n, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil {
		return 0, err
	}
	return n, nil
}

// bufferReadTimeout is how long we wait for the kernel to produce at least one
// frame before treating the capture path as stalled.
func bufferReadTimeout(hz int) time.Duration {
	if hz <= 0 {
		hz = 10
	}
	d := time.Duration(float64(time.Second) * 3 / float64(hz))
	if d < 750*time.Millisecond {
		return 750 * time.Millisecond
	}
	if d > 5*time.Second {
		return 5 * time.Second
	}
	return d
}

func recordToValues(rec iio.Record, colMap map[string]string) map[string]float64 {
	values := make(map[string]float64, len(rec.Values))
	for ch, v := range rec.Values {
		if ch == "timestamp_ns" {
			continue
		}
		v = units.NormalizeIIO(ch, v)
		col := colMap[ch]
		if col == "" {
			col = store.Sanitize(ch)
		}
		if canon := units.ColumnForIIO(ch); canon != "" {
			col = canon
		}
		values[col] = v
	}
	return values
}

// runBuffered captures via IIO buffer + hrtimer at dev.SampleHz. Falls back to
// polled runOne if setup fails (missing configfs, permissions, etc.).
func runBuffered(ctx context.Context, r *iioReader, name string, holder *config.Holder, hub *live.Hub, buf *store.Buffer, st *store.Store, reg *Registry) {
	chans := r.bufferChannels()
	if len(chans) == 0 {
		log.Printf("sensors: %s: no scan_elements — using polled reads", name)
		runOne(ctx, r, name, holder, hub, buf, st, reg)
		return
	}

	cfg := holder.Get()
	dev := cfg.DeviceOrDefault(r.Name(), 10)
	if !dev.WantBuffer(len(chans)) {
		runOne(ctx, r, name, holder, hub, buf, st, reg)
		return
	}

	paused := !dev.Enabled

	effectiveHz := clampBufferedHz(r, dev.SampleHz)
	if effectiveHz != dev.SampleHz {
		log.Printf("sensors: %s: sample_hz %.0f exceeds max %.1f for oversampling — using %.1f Hz",
			name, dev.SampleHz, effectiveHz, effectiveHz)
	}
	hz := int(math.Round(effectiveHz))
	if hz <= 0 {
		hz = 10
	}
	tname := triggerName(name)
	hasTriggerSysfs := r.hasPath("trigger/current_trigger")
	hwFIFO := !hasTriggerSysfs && usesHWFIFOBuffer(r.Name())
	var (
		trig        *iio.HRTrigger
		triggerName string
		err         error
	)
	if !paused {
		if hasTriggerSysfs {
			if chipTrig := discoverIIOTrigger(r.Name()); chipTrig != "" {
				triggerName = chipTrig
				log.Printf("sensors: %s: binding chip trigger %q", name, chipTrig)
			} else {
				trig, err = iio.EnsureHRTimer(tname, hz)
				if err != nil {
					log.Printf("sensors: %s: buffer trigger: %v — using polled reads", name, err)
					runOne(ctx, r, name, holder, hub, buf, st, reg)
					return
				}
				triggerName = trig.Name()
				defer releaseHRTimer(trig)
			}
		} else if hwFIFO {
			if err := syncDeviceSamplingHz(r, effectiveHz); err != nil {
				log.Printf("sensors: %s: sampling_frequency: %v", name, err)
			}
			log.Printf("sensors: %s: hwfifo buffer (INT1); no trigger/current_trigger", name)
		} else {
			log.Printf("sensors: %s: no trigger/current_trigger; trying device-native buffer", name)
		}
	} else {
		log.Printf("sensors: %s: disabled — paused until enabled in config", name)
	}

	blen := bufferLengthForHz(effectiveHz)
	var iobuf *iio.Buffer
	if !paused {
		iobuf, err = r.openIIOBuffer(chans, blen, triggerName)
		if err != nil {
			log.Printf("sensors: %s: open buffer: %v — using polled reads", name, err)
			runOne(ctx, r, name, holder, hub, buf, st, reg)
			return
		}
	}
	// Close whatever buffer iobuf points to AT RETURN TIME, not the one
	// captured now: restartCapture / cooldownAndRetryBuffered reassign
	// iobuf to a freshly opened buffer, and a bare `defer iobuf.Close()`
	// would close the original (already-closed) one and leak the live
	// reopened buffer — leaving buffer/enable=1 and the trigger bound in
	// sysfs on shutdown. The nil guard covers a failed-reopen exit.
	defer func() {
		if iobuf != nil {
			_ = iobuf.Close()
		}
	}()

	var clock string
	triggerLabel := triggerName
	if iobuf != nil {
		clock = iobuf.TimestampClock()
		switch {
		case triggerLabel != "":
		case hwFIFO:
			triggerLabel = "hwfifo-int1"
		default:
			triggerLabel = "device-native"
		}
		log.Printf("sensors: %s: IIO buffer %d frames @ %d Hz (trigger %s, clock %q, channels %v)",
			name, blen, hz, triggerLabel, clock, chans)
	}

	reload := holder.Subscribe()
	colMap := buildColumnMap(filterDataChannels(chans), dev.Channels)
	prevAttrs := SnapshotAttrs(r)
	// One frame per Read syscall — the kernel may block until the full
	// request size is available; large batches stall on slow sensors (BMP280).
	readBatch := 1
	recs := make([]iio.Record, readBatch)

	var (
		consecutiveStalls  int
		stallRestartCycles int
		fallbackToPolled   bool
	)
	const (
		maxStallRestartCycles = 8
		// initialFallbackCooldown is the first polled-mode burst length
		// after a buffered-capture exhaustion. Each successive failed
		// recovery attempt doubles the cooldown up to maxFallbackCooldown,
		// so a transient DMA stall no longer permanently drops the device
		// from 100 Hz buffered to ~10 Hz polled for the rest of the flight.
		initialFallbackCooldown = 30 * time.Second
		maxFallbackCooldown     = 5 * time.Minute
	)

	restartCapture := func(newDev config.Device) bool {
		if !newDev.Enabled {
			return false
		}
		if iobuf != nil {
			_ = iobuf.Close()
			iobuf = nil
		}
		if err := applyConfiguredAttrs(r, newDev); err != nil {
			log.Printf("sensors: %s reapply attrs: %v", name, err)
		}
		effectiveHz := clampBufferedHz(r, newDev.SampleHz)
		if hwFIFO {
			if err := syncDeviceSamplingHz(r, effectiveHz); err != nil {
				log.Printf("sensors: %s: sampling_frequency: %v", name, err)
			}
		}
		if effectiveHz != newDev.SampleHz {
			log.Printf("sensors: %s: sample_hz %.0f exceeds max %.1f for oversampling — using %.1f Hz",
				name, newDev.SampleHz, effectiveHz, effectiveHz)
		}
		newHz := int(math.Round(effectiveHz))
		if newHz <= 0 {
			newHz = 10
		}
		if trig != nil {
			if err := trig.SetFrequency(newHz); err != nil {
				log.Printf("sensors: %s: set trigger %d Hz: %v", name, newHz, err)
			}
		}
		hz = newHz
		blen = bufferLengthForHz(effectiveHz)
		var err error
		iobuf, err = r.openIIOBuffer(chans, blen, triggerName)
		if err != nil {
			log.Printf("sensors: %s: reopen buffer @ %d Hz: %v", name, hz, err)
			return false
		}
		clock = iobuf.TimestampClock()
		readBatch = 1
		recs = make([]iio.Record, readBatch)
		consecutiveStalls = 0
		log.Printf("sensors: %s: IIO buffer restarted %d frames @ %d Hz", name, blen, hz)
		return true
	}

	handleStall := func(err error) (retry bool, stop bool) {
		if ctx.Err() != nil {
			return false, true
		}
		consecutiveStalls++
		if consecutiveStalls >= 3 {
			log.Printf("sensors: %s: buffer stalled (%v) — restarting capture", name, err)
			consecutiveStalls = 0
			if restartCapture(dev) {
				stallRestartCycles++
				if stallRestartCycles >= maxStallRestartCycles {
					log.Printf("sensors: %s: buffer restarts exhausted — using polled reads", name)
					fallbackToPolled = true
					return false, true
				}
				return true, false
			}
			log.Printf("sensors: %s: buffer restart failed — using polled reads", name)
			fallbackToPolled = true
			return false, true
		}
		if consecutiveStalls == 1 {
			log.Printf("sensors: %s: buffer read: %v", name, err)
		}
		return false, false
	}

	for {
		if !dev.Enabled || iobuf == nil {
			select {
			case <-ctx.Done():
				return
			case <-reload:
				cfg = holder.Get()
				newDev := cfg.DeviceOrDefault(r.Name(), 10)
				if !newDev.Enabled {
					if iobuf != nil {
						_ = iobuf.Close()
						iobuf = nil
					}
					dev = newDev
					continue
				}
				if !dev.Enabled {
					log.Printf("sensors: %s enabled by config reload — resuming buffered capture", name)
				}
				if !newDev.WantBuffer(len(chans)) {
					log.Printf("sensors: %s: use_buffer off — restart kingfisher to switch to polled mode", name)
				}
				if !restartCapture(newDev) {
					continue
				}
				if iobuf != nil {
					clock = iobuf.TimestampClock()
				}
				curr := SnapshotAttrs(r)
				diff := DiffAttrs(prevAttrs, curr)
				if len(diff) > 0 && st != nil {
					if err := st.LogAttrs(r.Name(), location.Hub, diff); err != nil {
						log.Printf("sensors: %s log attr diff: %v", name, err)
					}
				}
				if reg != nil {
					reg.Update(r.Name(), curr)
				}
				prevAttrs = curr
				dev = newDev
				colMap = buildColumnMap(filterDataChannels(chans), dev.Channels)
			}
			continue
		}
		select {
		case <-ctx.Done():
			return
		case <-reload:
			cfg = holder.Get()
			newDev := cfg.DeviceOrDefault(r.Name(), 10)
			if !newDev.Enabled {
				log.Printf("sensors: %s disabled by config reload — pausing", name)
				if iobuf != nil {
					_ = iobuf.Close()
					iobuf = nil
				}
				dev = newDev
				continue
			}
			if !newDev.WantBuffer(len(chans)) {
				log.Printf("sensors: %s: use_buffer off — restart kingfisher to switch to polled mode", name)
			}
			if !restartCapture(newDev) {
				continue
			}
			clock = iobuf.TimestampClock()
			curr := SnapshotAttrs(r)
			diff := DiffAttrs(prevAttrs, curr)
			if len(diff) > 0 && st != nil {
				if err := st.LogAttrs(r.Name(), location.Hub, diff); err != nil {
					log.Printf("sensors: %s log attr diff: %v", name, err)
				}
			}
			if reg != nil {
				reg.Update(r.Name(), curr)
			}
			prevAttrs = curr
			dev = newDev
			colMap = buildColumnMap(filterDataChannels(chans), dev.Channels)
		default:
		}

		readTimeout := bufferReadTimeout(hz)
		frame := iobuf.Layout().FrameBytes
		// hwfifo drivers (inv_icm45600) push frames on read(); data_available
		// may stay zero until then — do not treat that as a stall.
		if frame > 0 && !hwFIFO {
			waitDeadline := time.Now().Add(readTimeout)
			for time.Now().Before(waitDeadline) {
				if ctx.Err() != nil {
					return
				}
				avail, aerr := r.bufferBytesAvailable()
				if aerr == nil && avail >= frame {
					break
				}
				time.Sleep(5 * time.Millisecond)
			}
			if avail, aerr := r.bufferBytesAvailable(); aerr != nil || avail < frame {
				retry, stop := handleStall(context.DeadlineExceeded)
				if stop {
					if fallbackToPolled {
						if !cooldownAndRetryBuffered(ctx, r, name, holder, hub, buf, st, reg, &iobuf, restartCapture, dev) {
							return
						}
						stallRestartCycles = 0
						fallbackToPolled = false
						continue
					}
					return
				}
				if retry {
					continue
				}
				continue
			}
		}

		readCtx, cancel := context.WithTimeout(ctx, readTimeout)
		n, err := iobuf.Read(readCtx, recs)
		cancel()
		if err != nil {
			retry, stop := handleStall(err)
			if stop {
				if fallbackToPolled {
					if !cooldownAndRetryBuffered(ctx, r, name, holder, hub, buf, st, reg, &iobuf, restartCapture, dev) {
						return
					}
					stallRestartCycles = 0
					fallbackToPolled = false
					continue
				}
				return
			}
			if retry {
				continue
			}
			continue
		}
		consecutiveStalls = 0
		stallRestartCycles = 0
		for i := 0; i < n; i++ {
			values := recordToValues(recs[i], colMap)
			if len(values) == 0 {
				continue
			}
			ts := sampleTimeNs(recs[i], clock)
			sm := live.Sample{Device: name, TsNs: ts, Values: values}
			hub.Publish(sm)
			if buf != nil {
				buf.Append(sm)
			}
		}
	}
}

func filterDataChannels(chans []string) []string {
	out := make([]string, 0, len(chans))
	for _, ch := range chans {
		if ch != "timestamp" {
			out = append(out, ch)
		}
	}
	return out
}

// cooldownAndRetryBuffered handles the post-exhaustion recovery loop. We
// close the current buffer, run polled mode for a bounded burst, then try
// to reopen the buffer; on success the caller resumes buffered capture,
// on failure the cooldown doubles up to maxFallbackCooldown. Returns true
// when buffered capture is back, false on ctx cancellation.
func cooldownAndRetryBuffered(
	ctx context.Context,
	r *iioReader, name string,
	holder *config.Holder, hub *live.Hub, buf *store.Buffer, st *store.Store, reg *Registry,
	iobuf **iio.Buffer,
	restartCapture func(config.Device) bool,
	dev config.Device,
) bool {
	if *iobuf != nil {
		_ = (*iobuf).Close()
		*iobuf = nil
	}
	cooldown := 30 * time.Second
	const maxCooldown = 5 * time.Minute
	for ctx.Err() == nil {
		log.Printf("sensors: %s: falling back to polled reads for %s before retrying buffered", name, cooldown)
		pctx, pcancel := context.WithTimeout(ctx, cooldown)
		runOne(pctx, r, name, holder, hub, buf, st, reg)
		pcancel()
		if ctx.Err() != nil {
			return false
		}
		// Live config may have changed during the polled burst — pick up
		// the current device snapshot so the reopen uses fresh attrs.
		curDev := holder.Get().DeviceOrDefault(r.Name(), 10)
		if !curDev.Enabled {
			return false
		}
		log.Printf("sensors: %s: retrying buffered capture", name)
		if restartCapture(curDev) {
			log.Printf("sensors: %s: buffered capture restored", name)
			return true
		}
		cooldown *= 2
		if cooldown > maxCooldown {
			cooldown = maxCooldown
		}
	}
	return false
}

// sampleTimeNs preserves kernel capture time when the driver timestamps the
// buffer on CLOCK_REALTIME. That is the preferred path once the Pi wall clock
// is GNSS-disciplined. If the driver exposes only a raw timestamp counter, or
// no timestamp at all, we fall back to that value or finally to time.Now().
func sampleTimeNs(rec iio.Record, clock string) int64 {
	if clock == "realtime" && !rec.Time.IsZero() {
		return rec.Time.UnixNano()
	}
	if v, ok := rec.Values["timestamp_ns"]; ok {
		return int64(v)
	}
	return time.Now().UnixNano()
}
