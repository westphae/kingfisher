package oled

import (
	"fmt"
	"log"
	"time"

	"github.com/warthog618/go-gpiocdev"
)

const (
	buttonPoll     = 40 * time.Millisecond
	buttonDebounce = 30 * time.Millisecond
	longPress      = time.Second
)

type pressKind int

const (
	pressNone pressKind = iota
	pressShort
	pressLong
)

type button struct {
	line      *gpiocdev.Line
	down      bool
	downAt    time.Time
	firedLong bool
}

func openButton(chip string, line int) (*button, error) {
	if line < 0 {
		return nil, nil
	}
	chips := []string{chip}
	if chip == "" {
		chips = []string{"gpiochip4", "gpiochip0"}
	}
	var last error
	for _, c := range chips {
		if c == "" {
			continue
		}
		l, err := gpiocdev.RequestLine(c, line, gpiocdev.AsInput, gpiocdev.WithPullUp)
		if err != nil {
			last = err
			continue
		}
		log.Printf("oled: button GPIO%d on %s (active-low)", line, c)
		return &button{line: l}, nil
	}
	if last == nil {
		last = fmt.Errorf("oled: no gpio chip for line %d", line)
	}
	return nil, last
}

func (b *button) Close() {
	if b != nil && b.line != nil {
		_ = b.line.Close()
	}
}

// poll returns a press once, edge-triggered. Active-low with pull-up.
func (b *button) poll(now time.Time) pressKind {
	if b == nil || b.line == nil {
		return pressNone
	}
	v, err := b.line.Value()
	if err != nil {
		return pressNone
	}
	pressed := v == 0
	if pressed && !b.down {
		b.down = true
		b.downAt = now
		b.firedLong = false
		return pressNone
	}
	if pressed && b.down && !b.firedLong && now.Sub(b.downAt) >= longPress {
		b.firedLong = true
		return pressLong
	}
	if !pressed && b.down {
		held := now.Sub(b.downAt)
		b.down = false
		if b.firedLong {
			return pressNone
		}
		if held >= buttonDebounce {
			return pressShort
		}
	}
	return pressNone
}
