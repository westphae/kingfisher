package ups

import (
	"fmt"
	"os"
	"strings"
)

// AC state comes from the x120x kernel driver's power_supply node, not from
// GPIO6 directly. The driver owns GPIO6 (AC detect) and GPIO16 (charge
// control) because it needs them to run the shutdown and charge policy, and
// a GPIO line can only have one owner — kingfisher claiming line 6 would make
// the driver's probe fail. Reading sysfs also gets us the driver's debounced
// view rather than a raw pin sample.
const acOnlinePath = "/sys/class/power_supply/x120x-ac/online"

// PLD is the power-loss-detect input, an interface for testability.
type PLD interface {
	// ACPresent reports whether external power is present.
	ACPresent() (bool, error)
	Close() error
}

type pldSysfs struct{ path string }

// openPLD probes the node once so a missing driver (module not loaded, HAT
// absent) is detected at open rather than on every sample.
func openPLD() (PLD, error) {
	p := &pldSysfs{path: acOnlinePath}
	if _, err := p.ACPresent(); err != nil {
		return nil, err
	}
	return p, nil
}

// ACPresent re-reads the file each call; a sysfs attribute value is generated
// at read time, so a cached descriptor would need seeking back to 0 anyway.
func (p *pldSysfs) ACPresent() (bool, error) {
	b, err := os.ReadFile(p.path)
	if err != nil {
		return false, err
	}
	switch s := strings.TrimSpace(string(b)); s {
	case "1":
		return true, nil
	case "0":
		return false, nil
	default:
		return false, fmt.Errorf("%s: unexpected content %q", p.path, s)
	}
}

func (p *pldSysfs) Close() error { return nil }
