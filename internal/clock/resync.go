package clock

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// ResyncLevel selects light (chronyc reselect) or full (helper script) recovery.
type ResyncLevel string

const (
	ResyncLight ResyncLevel = "light"
	ResyncFull  ResyncLevel = "full"
)

// ResyncResult captures discipline state before and after a resync attempt.
type ResyncResult struct {
	Level       ResyncLevel
	Before      DisciplineStatus
	After       DisciplineStatus
	Err         string
	SyncedAfter bool
}

// HelperInstalled reports whether the configured resync helper script exists.
func HelperInstalled(helper string) bool {
	helper = strings.TrimSpace(helper)
	if helper == "" {
		return false
	}
	info, err := os.Stat(helper)
	return err == nil && !info.IsDir()
}

// Reselect asks chrony to re-evaluate and select the best time source.
// chronyc write commands require root on default Debian chrony (501 Not authorised).
func Reselect(ctx context.Context) error {
	if _, err := exec.LookPath("chronyc"); err != nil {
		return fmt.Errorf("chronyc not found")
	}
	qctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := exec.CommandContext(qctx, "chronyc", "reselect").Run(); err == nil {
		return nil
	}
	return runChronycSudo(ctx, "reselect")
}

// Resync runs a light or full time-sync recovery and re-queries discipline.
func Resync(ctx context.Context, level ResyncLevel, helper string) ResyncResult {
	result := ResyncResult{
		Level:  level,
		Before: QueryDiscipline(ctx),
	}

	switch level {
	case ResyncLight:
		if err := Reselect(ctx); err != nil {
			result.Err = err.Error()
		}
	case ResyncFull:
		if !HelperInstalled(helper) {
			result.Err = fmt.Sprintf("resync helper not installed: %s", strings.TrimSpace(helper))
			result.After = result.Before
			return result
		}
		if err := runResyncHelper(ctx, helper); err != nil {
			result.Err = err.Error()
		}
	default:
		result.Err = fmt.Sprintf("unknown resync level %q", level)
		result.After = result.Before
		return result
	}

	waitCtx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	select {
	case <-waitCtx.Done():
	case <-time.After(5 * time.Second):
	}

	result.After = QueryDiscipline(ctx)
	result.SyncedAfter = result.After.Synced
	return result
}

func runChronycSudo(ctx context.Context, args ...string) error {
	if _, err := exec.LookPath("sudo"); err != nil {
		return fmt.Errorf("chronyc reselect requires sudo: %w", err)
	}
	qctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	cmdArgs := append([]string{"chronyc"}, args...)
	out, err := exec.CommandContext(qctx, "sudo", cmdArgs...).CombinedOutput()
	if err != nil {
		msg := strings.TrimSpace(string(out))
		if msg != "" {
			return fmt.Errorf("%s: %w", msg, err)
		}
		return err
	}
	return nil
}

func runResyncHelper(ctx context.Context, helper string) error {
	helper = strings.TrimSpace(helper)
	if helper == "" {
		return fmt.Errorf("resync helper not configured")
	}
	args := []string{helper}
	qctx, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
	out, err := exec.CommandContext(qctx, "sudo", args...).CombinedOutput()
	if err != nil {
		msg := strings.TrimSpace(string(out))
		if msg != "" {
			return fmt.Errorf("%s: %w", msg, err)
		}
		return err
	}
	return nil
}
