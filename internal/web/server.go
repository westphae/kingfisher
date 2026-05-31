// Package web serves the cockpit status UI and the JSON config API.
// Routes:
//
//	GET  /            — server-rendered index.html with the device list.
//	GET  /ws          — WebSocket; pushes live.Hub snapshots every 100ms.
//	GET  /api/config  — current config JSON.
//	POST /api/config  — replace config; persists to disk + signals reload.
//	GET  /api/status  — DB path, size, buffered rows, GPS fix state, clock health.
//	POST /api/compass/align — capture sensor→vehicle alignment (manual or GPS taxi).
//	POST /api/airspeed/zero — average pitot ΔP over 15s and save as zero offset.
package web

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"html/template"
	"io/fs"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"github.com/westphae/kingfisher/internal/clock"
	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/derive"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/location"
	"github.com/westphae/kingfisher/internal/pod"
	"github.com/westphae/kingfisher/internal/sensors"
	"github.com/westphae/kingfisher/internal/store"
)

//go:embed templates/*.html static/*
var assets embed.FS

type Server struct {
	cfg     *config.Holder
	hub     *live.Hub
	store   *store.Store
	buf     *store.Buffer
	gps     *gps.Client
	pod     *pod.Client
	reg     *sensors.Registry
	compass derive.CompassAligner

	tpl     *template.Template
	httpSrv *http.Server
	up      websocket.Upgrader
}

func New(cfg *config.Holder, hub *live.Hub, st *store.Store, buf *store.Buffer, gpsc *gps.Client, podc *pod.Client, reg *sensors.Registry, compass derive.CompassAligner) (*Server, error) {
	tpl, err := template.ParseFS(assets, "templates/*.html")
	if err != nil {
		return nil, err
	}
	return &Server{
		cfg:   cfg,
		hub:   hub,
		store: st,
		buf:   buf,
		gps:   gpsc,
		pod:   podc,
		reg:     reg,
		compass: compass,
		tpl:     tpl,
		up:      websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return true }},
	}, nil
}

// Run starts the HTTP server and blocks until stop is closed.
func (s *Server) Run(addr string, stop <-chan struct{}) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleIndex)
	mux.HandleFunc("/ws", s.handleWS)
	mux.HandleFunc("/api/config", s.handleConfig)
	mux.HandleFunc("/api/status", s.handleStatus)
	mux.HandleFunc("/api/devices", s.handleDevices)
	mux.HandleFunc("/api/devices/", s.handleDeviceSub)
	mux.HandleFunc("/api/recording", s.handleRecording)
	mux.HandleFunc("/api/compass/align", s.handleCompassAlign)
	mux.HandleFunc("/api/airspeed/zero", s.handleAirspeedZero)

	staticFS, err := fs.Sub(assets, "static")
	if err != nil {
		return err
	}
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.FS(staticFS))))

	s.httpSrv = &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-stop
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = s.httpSrv.Shutdown(ctx)
	}()
	log.Printf("web: listening on %s", addr)
	if err := s.httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		return err
	}
	return nil
}

type indexData struct {
	Aircraft string
	Devices  []string
}

func (s *Server) handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	snap := s.hub.SnapshotNow()
	devs := make([]string, 0, len(snap.Devices))
	for d := range snap.Devices {
		if pod.HideLegacyTab(d) {
			continue
		}
		devs = append(devs, d)
	}
	data := indexData{Aircraft: s.cfg.Get().Aircraft, Devices: devs}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := s.tpl.ExecuteTemplate(w, "index.html", data); err != nil {
		log.Printf("web: template: %v", err)
	}
}

func (s *Server) handleWS(w http.ResponseWriter, r *http.Request) {
	c, err := s.up.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer c.Close()
	c.SetReadDeadline(time.Now().Add(60 * time.Second))
	c.SetPongHandler(func(string) error { c.SetReadDeadline(time.Now().Add(60 * time.Second)); return nil })
	// Drain reads so the pong handler fires.
	go func() {
		for {
			if _, _, err := c.NextReader(); err != nil {
				return
			}
		}
	}()

	ch := s.hub.Subscribe()
	defer s.hub.Unsubscribe(ch)
	ping := time.NewTicker(20 * time.Second)
	defer ping.Stop()
	for {
		select {
		case snap, ok := <-ch:
			if !ok {
				return
			}
			c.SetWriteDeadline(time.Now().Add(5 * time.Second))
			if err := c.WriteJSON(snap); err != nil {
				return
			}
		case <-ping.C:
			c.SetWriteDeadline(time.Now().Add(5 * time.Second))
			if err := c.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}

func (s *Server) handleConfig(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, s.cfg.Get())
	case http.MethodPost:
		var nc config.Config
		if err := json.NewDecoder(r.Body).Decode(&nc); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		s.cfg.Set(&nc)
		if err := config.Save(s.cfg.Path(), &nc); err != nil {
			log.Printf("web: config save: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, map[string]string{"status": "ok"})
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	cfg := s.cfg.Get()
	st := map[string]any{
		"aircraft":         cfg.Aircraft,
		"aircraft_name":    cfg.AircraftName,
		"db_path":          s.store.Path(),
		"db_size_bytes":    s.store.Size(),
		"buffered_rows":    s.buf.BufferedRows(),
		"recording_paused": s.buf.Paused(),
	}
	if free, err := s.store.VolumeFreeBytes(); err == nil {
		st["db_volume_free_bytes"] = free
	}
	if s.gps != nil {
		fix := s.gps.LastFix()
		gpsView := map[string]any{
			"has_fix": fix.HasFix,
			"mode":    fix.Mode,
			"sats":    fix.SatsInUse,
			"lat":     fix.Lat,
			"lon":     fix.Lon,
			"alt_msl": fix.AltMSL,
		}
		if !fix.Time.IsZero() {
			gpsView["fix_time_utc"] = fix.Time.UTC().Format(time.RFC3339Nano)
		}
		st["gps"] = gpsView
		st["clock"] = clockStatusView(r.Context(), s.gps.ClockStatus())
	}
	if s.pod != nil {
		st["pod"] = s.pod.LinkStats()
	} else {
		st["pod"] = pod.LinkStats{Enabled: false}
	}
	writeJSON(w, st)
}

// handleDevices returns one entry per discovered IIO device, with the
// device's name, current configured rate, and whether it is enabled. The
// UI uses this to render per-device sample-rate inputs.
func (s *Server) handleDevices(w http.ResponseWriter, r *http.Request) {
	cfg := s.cfg.Get()
	names := s.cfg.IIODeviceNames()
	type deviceView struct {
		Name        string   `json:"name"`
		SampleHz    float64  `json:"sample_hz"`
		Enabled     bool     `json:"enabled"`
		MaxSampleHz *float64 `json:"max_sample_hz,omitempty"`
	}
	out := make([]deviceView, 0, len(names))
	for _, n := range names {
		d := cfg.DeviceOrDefault(n, 10)
		v := deviceView{Name: n, SampleHz: d.SampleHz, Enabled: d.Enabled}
		if s.reg != nil {
			if max, ok := s.reg.MaxBufferedHzFor(n); ok && max > 0 {
				v.MaxSampleHz = &max
			}
		}
		out = append(out, v)
	}
	writeJSON(w, out)
}

// handleDeviceSub dispatches /api/devices/{name}/attrs (GET and POST).
func (s *Server) handleDeviceSub(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/api/devices/")
	parts := strings.SplitN(rest, "/", 2)
	if len(parts) != 2 || parts[1] != "attrs" {
		http.NotFound(w, r)
		return
	}
	device := parts[0]
	if s.reg == nil {
		http.Error(w, "registry not configured", http.StatusServiceUnavailable)
		return
	}
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, s.deviceAttrsResponse(device))
	case http.MethodPost:
		var body struct {
			Channel string `json:"channel"`
			Attr    string `json:"attr"`
			Value   string `json:"value"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if device == gpsDeviceName && body.Attr == "rate_hz" {
			if err := s.persistGPSRate(body.Value); err != nil {
				log.Printf("web: gps rate_hz=%q: %v", body.Value, err)
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			writeJSON(w, s.deviceAttrsResponse(device))
			return
		}
		if device == pressAltDeviceName && body.Attr == "kollsman_inhg" {
			if err := s.persistPressAltKollsman(body.Value); err != nil {
				log.Printf("web: press_alt kollsman_inhg=%q: %v", body.Value, err)
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			writeJSON(w, s.deviceAttrsResponse(device))
			return
		}
		if device == pod.BatteryDeviceName && body.Attr == pod.AttrDesignCapacityMah {
			if err := s.persistPodBatteryCapacity(body.Value); err != nil {
				log.Printf("web: bq27441 design_capacity_mah=%q: %v", body.Value, err)
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			if err := s.reg.WriteAttr(device, body.Channel, body.Attr, body.Value); err != nil {
				log.Printf("web: %s/%s %s=%q: %v", device, body.Channel, body.Attr, body.Value, err)
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			if s.pod != nil {
				s.pod.RefreshRegistryViews()
			}
			writeJSON(w, s.deviceAttrsResponse(device))
			return
		}
		if s.isIIODevice(device) && sensors.IsDeviceConfigAttr(body.Attr) {
			if err := s.persistIIODeviceSetting(device, body.Attr, body.Value); err != nil {
				log.Printf("web: %s %s=%q: %v", device, body.Attr, body.Value, err)
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			writeJSON(w, s.deviceAttrsResponse(device))
			return
		}
		if err := s.reg.WriteAttr(device, body.Channel, body.Attr, body.Value); err != nil {
			log.Printf("web: %s/%s %s=%q: %v", device, body.Channel, body.Attr, body.Value, err)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		// Mirror the change into the live config so it persists across
		// restarts and survives a future config reload.
		if err := s.persistAttrChange(device, body.Channel, body.Attr, body.Value); err != nil {
			log.Printf("web: persist attr change: %v", err)
		}
		if s.pod != nil {
			s.pod.RefreshRegistryViews()
		}
		writeJSON(w, s.deviceAttrsResponse(device))
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

type deviceAttrsResponse struct {
	Location string             `json:"location"`
	Attrs    []sensors.AttrView `json:"attrs"`
}

func (s *Server) deviceAttrsResponse(device string) deviceAttrsResponse {
	if loc := derivedDeviceLocation(device); loc != "" {
		return deviceAttrsResponse{Location: loc, Attrs: s.deviceAttrViews(device)}
	}
	loc := location.Hub
	if s.reg != nil {
		if l := s.reg.Location(device); l != "" {
			loc = l
		} else if pod.IsTelemetryDevice(s.reg, device) {
			loc = location.Pod
		}
	} else if pod.IsTelemetryDevice(nil, device) {
		loc = location.Pod
	}
	return deviceAttrsResponse{Location: loc, Attrs: s.deviceAttrViews(device)}
}

func derivedDeviceLocation(device string) string {
	switch device {
	case "ahrs", pressAltDeviceName, "compass", "airspeed", "geo":
		return location.Calc
	default:
		return ""
	}
}

func (s *Server) isIIODevice(name string) bool {
	for _, n := range s.cfg.IIODeviceNames() {
		if n == name {
			return true
		}
	}
	return false
}

// gpsDeviceName is the hub/UI device id for the gpsd source (see internal/gps).
const gpsDeviceName = "gps"

// pressAltDeviceName is the derived pressure-altitude device id.
const pressAltDeviceName = "press_alt"

// gpsAttrViews exposes the recorded GPS rate as a settings dropdown. The
// receiver runs at a fixed rate; internal/gps decimates to this value.
func gpsAttrViews(rateHz float64) []sensors.AttrView {
	if rateHz <= 0 {
		rateHz = 10
	}
	return []sensors.AttrView{{
		Attr:     "rate_hz",
		Value:    strconv.FormatFloat(rateHz, 'f', -1, 64),
		Writable: true,
		Options:  []string{"5", "10"},
	}}
}

// pressAltAttrViews exposes the altimeter setting used to derive indicated altitude.
func pressAltAttrViews(kollsmanInHg float64) []sensors.AttrView {
	return []sensors.AttrView{{
		Attr:     "kollsman_inhg",
		Value:    strconv.FormatFloat(kollsmanInHg, 'f', 2, 64),
		Writable: true,
	}}
}

// deviceAttrViews returns registry attrs plus config sample_hz/enabled for IIO tabs.
func (s *Server) deviceAttrViews(device string) []sensors.AttrView {
	if device == gpsDeviceName {
		return gpsAttrViews(s.cfg.Get().GPS.RateHz)
	}
	if device == pressAltDeviceName {
		return pressAltAttrViews(s.cfg.Get().KollsmanInHg())
	}
	var base []sensors.AttrView
	if s.reg != nil {
		base = s.reg.Get(device)
	}
	if device == pod.BatteryDeviceName {
		base = mergeBatteryCapacityAttr(base, s.cfg.Get().PodBatteryCapacityMah())
	}
	if !s.isIIODevice(device) {
		return base
	}
	dev := s.cfg.Get().DeviceOrDefault(device, 10)
	var max float64
	if s.reg != nil {
		max, _ = s.reg.MaxBufferedHzFor(device)
	}
	return append(sensors.ConfigAttrViews(dev, max), base...)
}

func (s *Server) persistIIODeviceSetting(device, attr, value string) error {
	cur := s.cfg.Get()
	cp := *cur
	cp.Devices = make(map[string]config.Device, len(cur.Devices))
	for k, v := range cur.Devices {
		cp.Devices[k] = copyDevice(v)
	}
	d, exists := cp.Devices[device]
	if !exists {
		d = cur.DeviceOrDefault(device, 10)
	}
	switch attr {
	case "sample_hz":
		hz, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
		if err != nil || hz <= 0 {
			return fmt.Errorf("sample_hz: invalid %q", value)
		}
		if s.reg != nil {
			if max, ok := s.reg.MaxBufferedHzFor(device); ok && hz > max {
				return fmt.Errorf("sample_hz %.0f exceeds max %.0f for device", hz, max)
			}
		}
		d.SampleHz = hz
	case "enabled":
		v := strings.TrimSpace(strings.ToLower(value))
		d.Enabled = v == "true" || v == "1"
	default:
		return fmt.Errorf("unknown device setting %q", attr)
	}
	cp.Devices[device] = d
	s.cfg.Set(&cp)
	return config.Save(s.cfg.Path(), &cp)
}

// persistGPSRate stores the recorded GPS rate (Hz) in config and persists it.
// internal/gps reads cfg.GPS.RateHz live, so the change takes effect at once.
func (s *Server) persistGPSRate(value string) error {
	hz, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
	if err != nil || hz <= 0 {
		return fmt.Errorf("rate_hz: invalid %q", value)
	}
	cur := s.cfg.Get()
	cp := *cur
	cp.GPS = config.GPS{RateHz: hz}
	s.cfg.Set(&cp)
	return config.Save(s.cfg.Path(), &cp)
}

func (s *Server) handleCompassAlign(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if s.compass == nil {
		http.Error(w, "compass not enabled", http.StatusServiceUnavailable)
		return
	}
	var body struct {
		ManualHeadingDeg *float64 `json:"manual_heading_deg"`
		AlignMethod      string   `json:"align_method,omitempty"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	decl := 0.0
	snap := s.hub.SnapshotNow()
	if geo, ok := snap.Devices["geo"]; ok {
		decl = geo.Values["declination"]
	}
	if err := s.compass.Align(body.ManualHeadingDeg, s.gps.LastFix(), decl, body.AlignMethod, snap); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeJSON(w, map[string]string{"status": "ok"})
}

func (s *Server) handleAirspeedZero(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	avg, n, err := derive.SamplePitotDpAverage(r.Context(), s.hub, derive.AirspeedZeroSampleDuration)
	if err != nil {
		code := http.StatusServiceUnavailable
		if err == context.Canceled || err == context.DeadlineExceeded {
			code = http.StatusRequestTimeout
		}
		http.Error(w, err.Error(), code)
		return
	}
	cur := s.cfg.Get()
	cp := *cur
	cp.Airspeed = cur.Airspeed
	cp.Airspeed.DpZeroPa = avg
	s.cfg.Set(&cp)
	if err := config.Save(s.cfg.Path(), &cp); err != nil {
		log.Printf("web: airspeed zero save: %v", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(w, map[string]any{
		"status":     "ok",
		"dp_zero_pa": avg,
		"samples":    n,
		"duration_s": derive.AirspeedZeroSampleDuration.Seconds(),
	})
}

func (s *Server) persistPressAltKollsman(value string) error {
	inhg, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
	if err != nil || inhg < 20 || inhg > 35 {
		return fmt.Errorf("kollsman_inhg: invalid %q", value)
	}
	cur := s.cfg.Get()
	cp := *cur
	cp.PressAlt = cur.PressAlt
	cp.PressAlt.KollsmanInHg = inhg
	s.cfg.Set(&cp)
	return config.Save(s.cfg.Path(), &cp)
}

func (s *Server) persistPodBatteryCapacity(value string) error {
	mah64, err := strconv.ParseUint(strings.TrimSpace(value), 10, 16)
	if err != nil {
		return fmt.Errorf("design_capacity_mah: invalid %q", value)
	}
	mah := uint16(mah64)
	if mah < 100 || mah > 10000 {
		return fmt.Errorf("design_capacity_mah: %d out of range [100, 10000]", mah)
	}
	cur := s.cfg.Get()
	cp := *cur
	cp.Pod = copyPod(cur.Pod)
	cp.Pod.BatteryCapacityMah = mah
	s.cfg.Set(&cp)
	return config.Save(s.cfg.Path(), &cp)
}

func mergeBatteryCapacityAttr(base []sensors.AttrView, mah uint16) []sensors.AttrView {
	if mah == 0 {
		mah = config.DefaultPodBatteryCapacityMah
	}
	val := strconv.FormatUint(uint64(mah), 10)
	for i := range base {
		if base[i].Channel == "" && base[i].Attr == pod.AttrDesignCapacityMah {
			base[i].Value = val
			base[i].Writable = true
			return base
		}
	}
	return append(base, sensors.AttrView{
		Channel:  "",
		Attr:     pod.AttrDesignCapacityMah,
		Value:    val,
		Writable: true,
		Location: location.Pod,
	})
}

// persistAttrChange merges one attribute write into the live config's
// per-device Attrs map and writes the result to disk. The signal sent by
// Set causes the reader goroutine to re-apply the same value; that's a
// no-op at the sysfs level and produces no spurious sensor_attrs row.
func (s *Server) persistAttrChange(device, channel, attr, value string) error {
	cur := s.cfg.Get()
	cp := *cur
	key := sensors.JoinIIOAttr(channel, attr)
	if channel == "" {
		key = sensors.JoinIIOAttr(device, attr)
	}

	if pod.IsTelemetryDevice(s.reg, device) {
		cp.Pod = copyPod(cur.Pod)
		if cp.Pod.Attrs == nil {
			cp.Pod.Attrs = make(map[string]string)
		}
		cp.Pod.Attrs[key] = value
	}

	cp.Devices = make(map[string]config.Device, len(cur.Devices))
	for k, v := range cur.Devices {
		cp.Devices[k] = copyDevice(v)
	}
	d := cp.Devices[device]
	if _, exists := cp.Devices[device]; !exists {
		d = cur.DeviceOrDefault(device, 10)
	}
	if d.Attrs == nil {
		d.Attrs = make(map[string]string)
	}
	d.Attrs[key] = value
	cp.Devices[device] = d

	s.cfg.Set(&cp)
	return config.Save(s.cfg.Path(), &cp)
}

func copyPod(p config.Pod) config.Pod {
	out := p
	if len(p.Attrs) > 0 {
		out.Attrs = make(map[string]string, len(p.Attrs))
		for k, v := range p.Attrs {
			out.Attrs[k] = v
		}
	}
	return out
}

func copyDevice(d config.Device) config.Device {
	out := d
	if len(d.Attrs) > 0 {
		out.Attrs = make(map[string]string, len(d.Attrs))
		for k, v := range d.Attrs {
			out.Attrs[k] = v
		}
	}
	if len(d.Channels) > 0 {
		out.Channels = make(map[string]config.Channel, len(d.Channels))
		for k, v := range d.Channels {
			out.Channels[k] = v
		}
	}
	return out
}

// handleRecording reports or sets the paused state of the store buffer.
// GET returns {"paused": bool}; POST {"paused": bool} updates it.
func (s *Server) handleRecording(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, map[string]bool{"paused": s.buf.Paused()})
	case http.MethodPost:
		var body struct {
			Paused bool `json:"paused"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if err := s.buf.SetPaused(body.Paused); err != nil {
			log.Printf("web: recording pause: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, map[string]bool{"paused": body.Paused})
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	_ = enc.Encode(v)
}

func clockStatusView(ctx context.Context, st gps.ClockStatus) map[string]any {
	disc := clock.QueryDiscipline(ctx)

	out := map[string]any{
		"startup_fallback": st.StartupCheck.Fallback,
		"startup_state":    st.StartupCheck.State,
		"discipline":       disciplineView(disc),
		"gps_check":        gpsCrosscheckView(st),
	}
	if !st.StartupCheck.CheckedAt.IsZero() {
		out["startup_checked_at_utc"] = st.StartupCheck.CheckedAt.UTC().Format(time.RFC3339Nano)
	}
	if st.StartupCheck.Reason != "" {
		out["startup_reason"] = st.StartupCheck.Reason
	}
	if st.StartupCheck.HasFix {
		out["startup_offset_ms"] = float64(st.StartupCheck.Offset) / float64(time.Millisecond)
	}
	out["detail"] = clockDetailTooltip(disc, st)
	return out
}

func disciplineView(disc clock.DisciplineStatus) map[string]any {
	v := map[string]any{
		"available":    disc.Available,
		"synced":       disc.Synced,
		"source":       disc.Source,
		"source_label": disc.SourceLabel,
		"stratum":      disc.Stratum,
		"pps_present":  disc.PPSPresent,
		"pps_steering": disc.PPSSteering,
	}
	if disc.LastOffset != 0 || disc.Synced {
		v["last_offset_ns"] = disc.LastOffset.Nanoseconds()
	}
	if disc.RMSOffset != 0 || disc.Synced {
		v["rms_offset_ns"] = disc.RMSOffset.Nanoseconds()
	}
	return v
}

func gpsCrosscheckView(st gps.ClockStatus) map[string]any {
	v := map[string]any{
		"state":       st.State,
		"has_fix":     st.HasFix,
		"fresh":       st.Fresh,
		"disciplined": st.Disciplined,
	}
	if !st.FixTime.IsZero() {
		v["fix_time_utc"] = st.FixTime.UTC().Format(time.RFC3339Nano)
		v["fix_age_s"] = st.FixAge.Seconds()
		v["pipeline_lag_ms"] = float64(st.Offset) / float64(time.Millisecond)
		if st.Baseline != 0 {
			v["baseline_lag_ms"] = float64(st.Baseline) / float64(time.Millisecond)
		}
		v["clock_error_ms"] = float64(st.Skew) / float64(time.Millisecond)
		v["baseline_ready"] = st.BaselineReady
	}
	return v
}

func clockDetailTooltip(disc clock.DisciplineStatus, st gps.ClockStatus) string {
	var parts []string
	if disc.Available && disc.Synced {
		parts = append(parts, fmt.Sprintf("Pi wall clock steered by %s via chrony (stratum %d). Last correction: %s, RMS %s.",
			disc.SourceLabel, disc.Stratum, formatDurationHuman(disc.LastOffset), formatDurationHuman(disc.RMSOffset)))
		if disc.PPSPresent && !disc.PPSSteering {
			parts = append(parts, "PPS hardware is present but chrony is not steering from it.")
		}
	} else if disc.Available {
		parts = append(parts, "chrony is not synchronized to a time reference.")
	} else {
		parts = append(parts, "chrony status unavailable.")
	}
	if st.HasFix {
		if st.BaselineReady {
			parts = append(parts, fmt.Sprintf("GPS fix cross-check: Pi clock differs from fix epoch by %s after subtracting typical receiver lag (%s).",
				formatDurationHuman(st.Skew), formatDurationHuman(st.Baseline)))
		} else {
			parts = append(parts, fmt.Sprintf("GPS fix cross-check warming up (fix epoch lag %s).", formatDurationHuman(st.Offset)))
		}
		if !st.Fresh {
			parts = append(parts, fmt.Sprintf("Last GPS fix time is %.1f s old.", st.FixAge.Seconds()))
		}
	} else {
		parts = append(parts, "No GPS fix for cross-check.")
	}
	if st.StartupCheck.Fallback && st.StartupCheck.Reason != "" {
		parts = append(parts, "Startup: "+st.StartupCheck.Reason)
	}
	return strings.Join(parts, " ")
}

func formatDurationHuman(d time.Duration) string {
	abs := d
	sign := ""
	if d < 0 {
		abs = -d
		sign = "-"
	}
	switch {
	case abs < time.Microsecond:
		return fmt.Sprintf("%s%d ns", sign, abs.Nanoseconds())
	case abs < time.Millisecond:
		return fmt.Sprintf("%s%.1f µs", sign, float64(abs)/float64(time.Microsecond))
	case abs < time.Second:
		return fmt.Sprintf("%s%.2f ms", sign, float64(abs)/float64(time.Millisecond))
	default:
		return fmt.Sprintf("%s%.2f s", sign, float64(abs)/float64(time.Second))
	}
}
