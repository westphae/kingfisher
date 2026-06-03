package clock

import (
	"context"
	"log"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/store"
)

const (
	defaultAutoResyncCooldown = 300 * time.Second
	defaultAutoResyncMaxTries = 6
	autoNudgeInterval         = 30 * time.Second
)

// NudgeState is the live auto-resync view exposed to the web UI.
type NudgeState struct {
	AutoEnabled       bool
	LastAttempt       time.Time
	LastResult        string
	AttemptCount      int
	MaxAttempts       int
	Cooldown          time.Duration
	NextEligibleAt    time.Time
	FullAvailable     bool
	ResyncHelper      string
}

// AutoNudger periodically runs chronyc reselect when chrony is unsynced but GPS
// has a fresh fix.
type AutoNudger struct {
	mu sync.Mutex

	cfg    func() config.Clock
	helper func() string
	clock  func() gps.ClockStatus
	store  *store.Store

	lastAttempt    time.Time
	lastResult     string
	attemptCount   int
	lastResync     ResyncResult
	resyncInFlight bool
}

// NewAutoNudger builds an auto-resync loop. helper returns the configured full
// resync script path for manual recovery.
func NewAutoNudger(cfg func() config.Clock, helper func() string, clockFn func() gps.ClockStatus, st *store.Store) *AutoNudger {
	return &AutoNudger{
		cfg:    cfg,
		helper: helper,
		clock:  clockFn,
		store:  st,
	}
}

// Run polls for unsynced discipline and triggers light resync with cooldown.
func (n *AutoNudger) Run(ctx context.Context, stop <-chan struct{}) {
	ticker := time.NewTicker(autoNudgeInterval)
	defer ticker.Stop()
	for {
		select {
		case <-stop:
			return
		case <-ctx.Done():
			return
		case <-ticker.C:
			n.maybeAutoNudge(ctx)
		}
	}
}

func (n *AutoNudger) maybeAutoNudge(ctx context.Context) {
	cfg := n.cfg()
	if !cfg.AutoResyncEffective() {
		return
	}
	if !n.clock().HasFix || !n.clock().Fresh {
		return
	}

	disc := QueryDiscipline(ctx)
	if !disc.Available || disc.Synced {
		return
	}

	n.mu.Lock()
	cooldown := cfg.AutoResyncCooldownDuration()
	maxTries := cfg.AutoResyncMaxAttemptsEffective()
	if n.attemptCount >= maxTries {
		n.mu.Unlock()
		return
	}
	if !n.lastAttempt.IsZero() && time.Since(n.lastAttempt) < cooldown {
		n.mu.Unlock()
		return
	}
	n.mu.Unlock()

	result := n.runResync(ctx, ResyncLight, "auto")
	n.logMetadata(result, "auto")
	if result.SyncedAfter {
		src := result.After.SourceLabel
		if src == "" {
			src = result.After.Source
		}
		log.Printf("clock: auto-resync locked (%s)", src)
	}
}

// ManualResync runs a pilot-triggered light or full resync.
func (n *AutoNudger) ManualResync(ctx context.Context, level ResyncLevel) ResyncResult {
	result := n.runResync(ctx, level, "manual")
	n.logMetadata(result, "manual")
	return result
}

func (n *AutoNudger) runResync(ctx context.Context, level ResyncLevel, trigger string) ResyncResult {
	n.mu.Lock()
	if n.resyncInFlight {
		n.mu.Unlock()
		return ResyncResult{Level: level, Err: "resync already in progress"}
	}
	n.resyncInFlight = true
	n.mu.Unlock()

	defer func() {
		n.mu.Lock()
		n.resyncInFlight = false
		n.mu.Unlock()
	}()

	helper := ""
	if level == ResyncFull {
		helper = n.helper()
	}
	result := Resync(ctx, level, helper)

	n.mu.Lock()
	defer n.mu.Unlock()
	n.lastAttempt = time.Now()
	n.lastResync = result
	n.lastResult = formatNudgeResult(result, trigger)
	if trigger == "auto" {
		n.attemptCount++
	}
	return result
}

func formatNudgeResult(r ResyncResult, trigger string) string {
	level := string(r.Level)
	if r.Err != "" {
		return trigger + ":" + level + ":error:" + r.Err
	}
	if r.SyncedAfter {
		return trigger + ":" + level + ":synced"
	}
	return trigger + ":" + level + ":still_unsynced"
}

func (n *AutoNudger) logMetadata(r ResyncResult, trigger string) {
	if n.store == nil {
		return
	}
	now := time.Now().UTC().Format(time.RFC3339)
	prefix := "clock_nudge_" + trigger + "_"
	_ = n.store.SetMeta(prefix+"at", now)
	_ = n.store.SetMeta(prefix+"level", string(r.Level))
	_ = n.store.SetMeta(prefix+"result", formatNudgeResult(r, trigger))
	_ = n.store.SetMeta(prefix+"synced_after", strconv.FormatBool(r.SyncedAfter))
}

// State returns the current auto-nudge view for the status API.
func (n *AutoNudger) State(now time.Time) NudgeState {
	cfg := n.cfg()
	cooldown := cfg.AutoResyncCooldownDuration()
	maxTries := cfg.AutoResyncMaxAttemptsEffective()
	helper := strings.TrimSpace(n.helper())

	n.mu.Lock()
	defer n.mu.Unlock()

	st := NudgeState{
		AutoEnabled:   cfg.AutoResyncEffective(),
		LastAttempt:   n.lastAttempt,
		LastResult:    n.lastResult,
		AttemptCount:  n.attemptCount,
		MaxAttempts:   maxTries,
		Cooldown:      cooldown,
		ResyncHelper:  helper,
		FullAvailable: HelperInstalled(helper),
	}
	if !n.lastAttempt.IsZero() {
		st.NextEligibleAt = n.lastAttempt.Add(cooldown)
	}
	return st
}
