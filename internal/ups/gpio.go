package ups

import (
	"github.com/warthog618/go-gpiocdev"
)

// The X1200 drives GPIO6 high while external power (ship bus / adapter) is
// present and low on power loss. GPIO16 is the HAT's charge control (drive
// high = disable charging) — it is deliberately NEVER claimed here: unclaimed
// with the Pi's pull-down it stays at the board default, charging enabled.
const (
	gpioChip = "gpiochip0" // pinctrl-rp1 (Pi 5 header GPIOs)
	pldLine  = 6
)

// PLD is the power-loss-detect input, an interface for testability.
type PLD interface {
	// ACPresent reports whether external power is present.
	ACPresent() (bool, error)
	Close() error
}

type pldGPIO struct {
	line *gpiocdev.Line
}

func openPLD() (PLD, error) {
	l, err := gpiocdev.RequestLine(gpioChip, pldLine,
		gpiocdev.AsInput,
		gpiocdev.WithPullUp,
		gpiocdev.WithConsumer("kingfisher-ups"))
	if err != nil {
		return nil, err
	}
	return &pldGPIO{line: l}, nil
}

func (p *pldGPIO) ACPresent() (bool, error) {
	v, err := p.line.Value()
	if err != nil {
		return false, err
	}
	return v != 0, nil
}

func (p *pldGPIO) Close() error { return p.line.Close() }
