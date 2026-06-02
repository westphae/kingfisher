// kingfisher records IIO + GPS streams to a per-flight SQLite file and
// serves a mobile cockpit status UI. See plan in
// /home/eric/.claude/plans/this-is-a-project-glowing-steele.md.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	"github.com/westphae/kingfisher/internal/clock"
	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/derive"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
	"github.com/westphae/kingfisher/internal/sensors"
	"github.com/westphae/kingfisher/internal/store"
	"github.com/westphae/kingfisher/internal/web"
)

const version = "0.1.0"

func main() {
	cfgPath := flag.String("config", config.DefaultPath(), "path to JSON config")
	webDev := flag.Bool("web-dev", false, "serve cockpit UI from internal/web on disk (CSS/JS edits apply on browser refresh without rebuild)")
	webDevDir := flag.String("web-dev-dir", "", "path to internal/web for -web-dev (default: auto-detect from cwd)")
	flag.Parse()

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	holder := config.NewHolder(*cfgPath, cfg)

	startupClock := gps.ProbeStartupClock(context.Background(), cfg.GPSDAddr)
	if startupClock.Fallback {
		log.Printf("clock: startup fell back to local wall time: %s", startupClock.Summary())
	} else {
		log.Printf("clock: startup assessment: %s", startupClock.Summary())
	}

	st, err := store.Open(cfg.DBDir, cfg.Aircraft)
	if err != nil {
		log.Fatalf("store: %v", err)
	}
	defer st.Close()
	log.Printf("kingfisher v%s flight DB: %s", version, st.Path())
	if err := st.SetMeta("clock_startup_state", startupClock.State); err != nil {
		log.Printf("store: clock_startup_state: %v", err)
	}
	if err := st.SetMeta("clock_startup_fallback", strconv.FormatBool(startupClock.Fallback)); err != nil {
		log.Printf("store: clock_startup_fallback: %v", err)
	}
	if startupClock.Reason != "" {
		if err := st.SetMeta("clock_startup_reason", startupClock.Reason); err != nil {
			log.Printf("store: clock_startup_reason: %v", err)
		}
	}
	if startupClock.HasFix {
		offsetMs := fmt.Sprintf("%.1f", float64(startupClock.Offset)/float64(time.Millisecond))
		if err := st.SetMeta("clock_startup_offset_ms", offsetMs); err != nil {
			log.Printf("store: clock_startup_offset_ms: %v", err)
		}
	}
	for k, v := range clock.StartupMeta(clock.QueryDiscipline(context.Background())) {
		if err := st.SetMeta(k, v); err != nil {
			log.Printf("store: %s: %v", k, err)
		}
	}
	if err := st.WriteSession(cfg.Aircraft, cfg.AircraftName, cfg.Notes, version); err != nil {
		log.Printf("store: write session: %v", err)
	}

	flushInterval := time.Duration(cfg.FlushSeconds) * time.Second
	if flushInterval <= 0 {
		flushInterval = 5 * time.Second
	}
	buf := store.NewBuffer(st, flushInterval)

	hub := live.NewHub()

	log.Printf("sensors: opening IIO devices…")
	readers, err := sensors.Open()
	if err != nil {
		log.Printf("sensors: %v", err)
	}
	log.Printf("sensors: %d device(s) discovered", len(readers))

	registry := sensors.NewRegistry()

	gpsClient := gps.New(cfg.GPSDAddr, hub, buf, func() float64 { return holder.Get().GPS.RateHz }, startupClock)

	var podClient *pod.Client
	podAddr := cfg.PodListenAddr()
	if podAddr != "" {
		t, err := pod.ListenUDP(podAddr)
		if err != nil {
			log.Printf("pod: %v (continuing without pod)", err)
		} else {
			podClient = pod.New(podAddr, t, hub, buf, st, registry, holder)
			log.Printf("pod: listening on %s", podAddr)
		}
	}

	ctx, cancel := context.WithCancel(context.Background())
	stop := make(chan struct{})
	go func() {
		c := make(chan os.Signal, 1)
		signal.Notify(c, os.Interrupt, syscall.SIGTERM)
		<-c
		log.Printf("shutdown requested")
		cancel()
		close(stop)
	}()

	var compassEngine *derive.Engine
	if cfg.Compass.Enabled {
		compassEngine = derive.CompassFromHub(ctx, holder, hub, gpsClient, buf)
	}

	devRoot := ""
	if *webDev {
		devRoot = *webDevDir
		if devRoot == "" {
			devRoot = web.FindDevWebRoot(".")
		}
		if devRoot == "" {
			log.Fatal("web-dev: could not find internal/web (set -web-dev-dir explicitly)")
		}
	}

	srv, err := web.New(holder, hub, st, buf, gpsClient, podClient, registry, compassEngine, devRoot)
	if err != nil {
		log.Fatalf("web: %v", err)
	}

	var wg sync.WaitGroup
	wg.Add(1)
	go func() { defer wg.Done(); hub.Run(stop) }()
	wg.Add(1)
	go func() { defer wg.Done(); buf.Run(stop) }()
	wg.Add(1)
	go func() { defer wg.Done(); gpsClient.Run(stop) }()
	if podClient != nil {
		wg.Add(1)
		go func() { defer wg.Done(); podClient.Run(stop) }()
	}
	wg.Add(1)
	go func() { defer wg.Done(); derive.AltitudeFromHub(ctx, holder, hub, buf, st) }()
	wg.Add(1)
	go func() { defer wg.Done(); derive.AirspeedFromHub(ctx, holder, hub, buf) }()
	wg.Add(1)
	go func() { defer wg.Done(); derive.DeclinationFromGPS(ctx, gpsClient, hub, buf) }()
	if cfg.AHRS.Enabled {
		wg.Add(1)
		go func() { defer wg.Done(); derive.AHRSFromHub(ctx, cfg.AHRS.RateHz, hub, gpsClient, buf) }()
	}
	wg.Add(1)
	go func() {
		defer wg.Done()
		sensors.Run(ctx, holder, readers, hub, buf, st, registry)
	}()
	wg.Add(1)
	go func() {
		defer wg.Done()
		if err := srv.Run(cfg.HTTPAddr, stop); err != nil {
			log.Printf("web: %v", err)
		}
	}()

	wg.Wait()
	log.Printf("kingfisher: shutdown complete")
}
