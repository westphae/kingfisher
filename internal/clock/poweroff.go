package clock

import (
	"context"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// PowerOff runs the configured helper after kingfisher has flushed and closed
// its flight DB. The helper should sync the filesystem and invoke systemctl
// poweroff (see deploy/kingfisher-poweroff.sh).
func PowerOff(ctx context.Context, helper string) error {
	helper = strings.TrimSpace(helper)
	if !HelperInstalled(helper) {
		return fmt.Errorf("poweroff helper not installed: %s", helper)
	}
	return runPoweroffHelper(ctx, helper)
}

func runPoweroffHelper(ctx context.Context, helper string) error {
	qctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	out, err := exec.CommandContext(qctx, "sudo", helper).CombinedOutput()
	if err != nil {
		msg := strings.TrimSpace(string(out))
		if msg != "" {
			return fmt.Errorf("%s: %w", msg, err)
		}
		return err
	}
	return nil
}
