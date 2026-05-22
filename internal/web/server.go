// Package web serves the cockpit status UI and the JSON config API.
// Routes:
//   GET  /            — server-rendered index.html with the device list.
//   GET  /ws          — WebSocket; pushes live.Hub snapshots every 100ms.
//   GET  /api/config  — current config JSON.
//   POST /api/config  — replace config; persists to disk + signals reload.
//   GET  /api/status  — DB path, size, buffered rows, GPS fix state.
package web

import (
	"context"
	"embed"
	"encoding/json"
	"html/template"
	"io/fs"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
	"github.com/westphae/kingfisher/internal/sensors"
	"github.com/westphae/kingfisher/internal/store"
)

//go:embed templates/*.html static/*
var assets embed.FS

type Server struct {
	cfg   *config.Holder
	hub   *live.Hub
	store *store.Store
	buf   *store.Buffer
	gps   *gps.Client
	pod   *pod.Client
	reg   *sensors.Registry

	tpl     *template.Template
	httpSrv *http.Server
	up      websocket.Upgrader
}

func New(cfg *config.Holder, hub *live.Hub, st *store.Store, buf *store.Buffer, gpsc *gps.Client, podc *pod.Client, reg *sensors.Registry) (*Server, error) {
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
		reg:   reg,
		tpl:   tpl,
		up:    websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return true }},
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
		st["gps"] = map[string]any{
			"has_fix": fix.HasFix,
			"mode":    fix.Mode,
			"sats":    fix.SatsInUse,
			"lat":     fix.Lat,
			"lon":     fix.Lon,
			"alt_msl": fix.AltMSL,
		}
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
		Name     string  `json:"name"`
		SampleHz float64 `json:"sample_hz"`
		Enabled  bool    `json:"enabled"`
	}
	out := make([]deviceView, 0, len(names))
	for _, n := range names {
		d := cfg.DeviceOrDefault(n, 10)
		out = append(out, deviceView{Name: n, SampleHz: d.SampleHz, Enabled: d.Enabled})
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
		writeJSON(w, s.reg.Get(device))
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
		if err := s.reg.WriteAttr(device, body.Channel, body.Attr, body.Value); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		// Mirror the change into the live config so it persists across
		// restarts and survives a future config reload.
		if err := s.persistAttrChange(device, body.Channel, body.Attr, body.Value); err != nil {
			log.Printf("web: persist attr change: %v", err)
		}
		writeJSON(w, s.reg.Get(device))
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

// persistAttrChange merges one attribute write into the live config's
// per-device Attrs map and writes the result to disk. The signal sent by
// Set causes the reader goroutine to re-apply the same value; that's a
// no-op at the sysfs level and produces no spurious sensor_attrs row.
func (s *Server) persistAttrChange(device, channel, attr, value string) error {
	cur := s.cfg.Get()
	cp := *cur
	key := sensors.JoinIIOAttr(channel, attr)

	if device == config.PodDeviceName {
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
		s.buf.SetPaused(body.Paused)
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
