package oled

import (
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/health"
	"github.com/westphae/kingfisher/internal/live"
)

const glyphW = 21

// View is the renderer input (health + one cycle value + clock/tail).
type View struct {
	Health    health.Report
	AlertIdx  int
	CycleText string
	ClockHHMM string
	Tail      string // aircraft id, truncated
	Shift     int
	ExtraMiss int
}

// Render paints the home layout onto a new Frame.
func Render(v View) Frame {
	var f Frame
	drawGlyphs(&f, v.Health.Checks)
	drawEnergy(&f, v.Health.Energy)
	text, fail := health.AlertLine(v.Health, v.AlertIdx)
	drawAlert(&f, text, fail)
	if v.ExtraMiss == 0 {
		v.ExtraMiss = health.ExtraMissing(v.Health)
	}
	drawCycle(&f, v.CycleText, v.ExtraMiss)
	drawFooter(&f, v.ClockHHMM, v.Tail)
	if v.Shift != 0 {
		f.Shift(v.Shift)
	}
	return f
}

func drawGlyphs(f *Frame, checks []health.Check) {
	for i, c := range checks {
		x0 := i * glyphW
		x1 := x0 + glyphW - 2
		switch c.Level {
		case health.LevelFail:
			f.FillRect(x0, 0, x1, 7, true)
			var g Frame
			g.Text6x8(x0+1, 0, c.Label)
			for y := 0; y <= 7; y++ {
				for x := x0; x <= x1; x++ {
					p, bit := y/8, byte(1)<<uint(y%8)
					if g.Pages[p][x]&bit != 0 {
						f.Set(x, y, false)
					}
				}
			}
		case health.LevelWarn:
			f.StrokeRect(x0, 0, x1, 7)
			f.Text6x8(x0+1, 0, c.Label)
		default:
			f.Text6x8(x0+1, 0, c.Label)
		}
	}
}

func drawEnergy(f *Frame, e health.Energy) {
	line := "POD " + e.POD
	f.Text6x8(0, 10, line)
	right := "UPS " + e.UPS
	x := width - len(strings.ToUpper(right))*6
	if x < 64 {
		x = 64
	}
	f.Text6x8(x, 10, right)
}

func drawAlert(f *Frame, text string, fail bool) {
	if len(text) > 10 {
		text = text[:10]
	}
	w := len(text) * 12
	x := (width - w) / 2
	if x < 0 {
		x = 0
	}
	if fail {
		f.FillRect(0, 22, width-1, 39, true)
		var g Frame
		g.Text12x16(x, 24, text)
		for y := 22; y <= 39; y++ {
			for col := 0; col < width; col++ {
				p, bit := y/8, byte(1)<<uint(y%8)
				if g.Pages[p][col]&bit != 0 {
					f.Set(col, y, false)
				}
			}
		}
		return
	}
	f.Text12x16(x, 24, text)
}

func drawCycle(f *Frame, text string, extraMiss int) {
	if extraMiss > 0 {
		text = strings.TrimSpace(text + " +" + itoa(extraMiss))
	}
	if text == "" {
		return
	}
	if len(text) > 21 {
		text = text[:21]
	}
	f.Text6x8(0, 42, text)
}

func drawFooter(f *Frame, hhmm, tail string) {
	f.Text6x8(0, 56, hhmm)
	tail = strings.ToUpper(strings.TrimSpace(tail))
	if tail == "" {
		return
	}
	if len(tail) > 10 {
		tail = tail[:10]
	}
	x := width - len(tail)*6
	f.Text6x8(x, 56, tail)
}

func itoa(n int) string {
	return fmt.Sprintf("%d", n)
}

func ClockHHMM(now time.Time) string {
	return now.UTC().Format("15:04")
}

// FormatCycle renders one hub channel for the 21-char cycle band.
func FormatCycle(hub live.Snapshot, item config.OLEDCycleItem) string {
	if item.Device == "" || item.Channel == "" {
		return ""
	}
	sm, ok := hub.Devices[item.Device]
	if !ok {
		return strings.ToUpper(shortDev(item.Device) + " -")
	}
	v, ok := sm.Values[item.Channel]
	if !ok || math.IsNaN(v) {
		return strings.ToUpper(shortDev(item.Device) + " -")
	}
	label := shortDev(item.Device)
	return strings.ToUpper(label + " " + formatValue(item.Channel, v))
}

func shortDev(name string) string {
	if len(name) <= 6 {
		return name
	}
	return name[:6]
}

func formatValue(ch string, v float64) string {
	switch {
	case strings.HasSuffix(ch, "_kt") || ch == "gs":
		if ch == "gs" {
			v *= 1.94384
		}
		return fmt.Sprintf("%.0f kt", v)
	case strings.HasSuffix(ch, "_ft") || ch == "alt_msl":
		if ch == "alt_msl" {
			v *= 3.28084
		}
		return fmt.Sprintf("%.0f ft", v)
	case strings.HasSuffix(ch, "_c"):
		return fmt.Sprintf("%.0fC", v)
	case strings.HasSuffix(ch, "_pa"):
		return fmt.Sprintf("%.0fPa", v)
	case strings.HasSuffix(ch, "_v") || ch == "supply_v":
		return fmt.Sprintf("%.1fV", v)
	case strings.HasSuffix(ch, "_pct") || ch == "sats":
		return fmt.Sprintf("%.0f", v)
	case strings.HasSuffix(ch, "_deg") || ch == "track" || ch == "roll" || ch == "pitch" || ch == "yaw":
		return fmt.Sprintf("%.0f deg", v)
	case strings.HasSuffix(ch, "_ut"):
		return fmt.Sprintf("%.1f uT", v)
	default:
		if math.Abs(v) >= 100 {
			return fmt.Sprintf("%.0f", v)
		}
		return fmt.Sprintf("%.1f", v)
	}
}

func pickCycle(hub live.Snapshot, items []config.OLEDCycleItem, idx int) string {
	if len(items) == 0 {
		return ""
	}
	if idx < 0 {
		idx = 0
	}
	return FormatCycle(hub, items[idx%len(items)])
}
