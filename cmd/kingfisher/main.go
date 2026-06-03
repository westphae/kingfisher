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
	"runtime/debug"
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

// safeGo runs fn in a new goroutine that recovers from panics. A single
// bad worker (nil-map access in a derive loop, sensor read crash, etc.)
// should not take down the flight recorder mid-flight — the other
// workers keep recording. The panicking worker still exits; we don't try
// to restart it because its state may be unsafe to reuse.
//
// critical workers are the exception: hub and store_buffer have no
// redundancy and their silent death turns the recorder into a zombie that
// looks alive (green UI) while recording nothing or freezing the live feed.
// For those, after logging+recording the panic we os.Exit(1) so the
// supervisor (systemd) restarts the process onto a fresh flight DB — a
// clean crash is strictly better than a silent zombie for a data recorder.
func safeGo(wg *sync.WaitGroup, name string, critical bool, st *store.Store, fn func()) {
	wg.Add(1)
	go func() {
		defer wg.Done()
		defer func() {
			if r := recover(); r != nil {
				log.Printf("PANIC in %s: %v\n%s", name, r, debug.Stack())
				if st != nil {
					_ = st.SetMeta("panic_"+name, fmt.Sprintf("%v", r))
				}
				if critical {
					log.Printf("FATAL: critical worker %s died; exiting for supervisor restart", name)
					os.Exit(1)
				}
			}
		}()
		fn()
	}()
}

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
	// Shutdown ordering: buf.Run is one of the workers in wg, so wg.Wait()
	// below blocks until its final Flush() returns. Only after that does
	// this deferred st.Close() run, which means flushed rows reach disk
	// (via the WAL checkpoint in Close) before the DB handle is released.
	// Do not reorder these.
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

	autoNudger := clock.NewAutoNudger(
		func() config.Clock { return holder.Get().Clock },
		func() string { return holder.Get().Clock.ResyncHelper },
		func() gps.ClockStatus { return gpsClient.ClockStatus() },
		st,
	)

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

	srv, err := web.New(holder, hub, st, buf, gpsClient, podClient, registry, compassEngine, autoNudger, devRoot)
	if err != nil {
		log.Fatalf("web: %v", err)
	}

	var wg sync.WaitGroup
	// hub and store_buffer are critical: their silent death zombifies the
	// recorder, so a panic there is fatal (systemd restarts). All others
	// are isolated — losing one source/derived stream still records the rest.
	safeGo(&wg, "hub", true, st, func() { hub.Run(stop) })
	safeGo(&wg, "store_buffer", true, st, func() { buf.Run(stop) })
	safeGo(&wg, "gps", false, st, func() { gpsClient.Run(stop) })
	safeGo(&wg, "clock_auto_nudge", false, st, func() { autoNudger.Run(ctx, stop) })
	if podClient != nil {
		safeGo(&wg, "pod", false, st, func() { podClient.Run(stop) })
	}
	safeGo(&wg, "derive_altitude", false, st, func() { derive.AltitudeFromHub(ctx, holder, hub, buf, st) })
	safeGo(&wg, "derive_airspeed", false, st, func() { derive.AirspeedFromHub(ctx, holder, hub, buf) })
	safeGo(&wg, "derive_declination", false, st, func() { derive.DeclinationFromGPS(ctx, gpsClient, hub, buf) })
	if cfg.AHRS.Enabled {
		safeGo(&wg, "derive_ahrs", false, st, func() { derive.AHRSFromHub(ctx, holder, cfg.AHRS.RateHz, hub, gpsClient, buf) })
	}
	safeGo(&wg, "sensors", false, st, func() { sensors.Run(ctx, holder, readers, hub, buf, st, registry) })
	safeGo(&wg, "web", false, st, func() {
		if err := srv.Run(cfg.HTTPAddr, stop); err != nil {
			log.Printf("web: %v", err)
		}
	})

	wg.Wait()
	log.Printf("kingfisher: shutdown complete")
}
