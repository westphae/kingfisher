// kingfisher records IIO + GPS streams to a per-flight SQLite file and
// serves a mobile cockpit status UI. See plan in
// /home/eric/.claude/plans/this-is-a-project-glowing-steele.md.
package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

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
	flag.Parse()

	cfg, err := config.Load(*cfgPath)
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	holder := config.NewHolder(*cfgPath, cfg)

	st, err := store.Open(cfg.DBDir, cfg.Aircraft)
	if err != nil {
		log.Fatalf("store: %v", err)
	}
	defer st.Close()
	log.Printf("kingfisher v%s flight DB: %s", version, st.Path())
	if err := st.WriteSession(cfg.Aircraft, cfg.AircraftName, cfg.Notes, version); err != nil {
		log.Printf("store: write session: %v", err)
	}

	flushInterval := time.Duration(cfg.FlushSeconds) * time.Second
	if flushInterval <= 0 {
		flushInterval = 5 * time.Second
	}
	buf := store.NewBuffer(st, flushInterval)

	hub := live.NewHub()

	readers, err := sensors.Open()
	if err != nil {
		log.Printf("sensors: %v", err)
	}
	log.Printf("sensors: %d device(s) discovered", len(readers))

	registry := sensors.NewRegistry()

	gpsClient := gps.New(cfg.GPSDAddr, hub, buf)

	var podClient *pod.Client
	podAddr := cfg.PodListenAddr()
	if podAddr != "" {
		t, err := pod.ListenUDP(podAddr)
		if err != nil {
			log.Printf("pod: %v (continuing without pod)", err)
		} else {
			podClient = pod.New(podAddr, t, hub, buf, registry, holder)
			log.Printf("pod: listening on %s", podAddr)
		}
	}

	srv, err := web.New(holder, hub, st, buf, gpsClient, registry)
	if err != nil {
		log.Fatalf("web: %v", err)
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
	go func() { defer wg.Done(); derive.AltitudeFromHub(ctx, hub, buf) }()
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
