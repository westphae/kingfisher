package oled

import "strings"

// Frame is a 128×64 1-bit buffer stored as SSD1306 pages (8 rows per page).
type Frame struct {
	Pages [pages][width]byte
}

func (f *Frame) Clear() {
	*f = Frame{}
}

func (f *Frame) Set(x, y int, on bool) {
	if x < 0 || x >= width || y < 0 || y >= 64 {
		return
	}
	p, bit := y/8, byte(1)<<uint(y%8)
	if on {
		f.Pages[p][x] |= bit
	} else {
		f.Pages[p][x] &^= bit
	}
}

func (f *Frame) FillRect(x0, y0, x1, y1 int, on bool) {
	if x0 > x1 {
		x0, x1 = x1, x0
	}
	if y0 > y1 {
		y0, y1 = y1, y0
	}
	for y := y0; y <= y1; y++ {
		for x := x0; x <= x1; x++ {
			f.Set(x, y, on)
		}
	}
}

func (f *Frame) StrokeRect(x0, y0, x1, y1 int) {
	for x := x0; x <= x1; x++ {
		f.Set(x, y0, true)
		f.Set(x, y1, true)
	}
	for y := y0; y <= y1; y++ {
		f.Set(x0, y, true)
		f.Set(x1, y, true)
	}
}

func (f *Frame) InvertRect(x0, y0, x1, y1 int) {
	for y := y0; y <= y1; y++ {
		for x := x0; x <= x1; x++ {
			if x < 0 || x >= width || y < 0 || y >= 64 {
				continue
			}
			p, bit := y/8, byte(1)<<uint(y%8)
			f.Pages[p][x] ^= bit
		}
	}
}

// Text6x8 draws uppercase 5×7 glyphs in 6×8 cells. Returns the x after the last glyph.
func (f *Frame) Text6x8(x, y int, s string) int {
	s = strings.ToUpper(s)
	for _, r := range s {
		g := glyph(r)
		for col := 0; col < 5; col++ {
			bits := g[col]
			for row := 0; row < 7; row++ {
				if bits&(1<<uint(row)) != 0 {
					f.Set(x+col, y+row, true)
				}
			}
		}
		x += 6
	}
	return x
}

// Text12x16 draws a 2× scaled 5×7 font in 12×16 cells.
func (f *Frame) Text12x16(x, y int, s string) int {
	s = strings.ToUpper(s)
	for _, r := range s {
		g := glyph(r)
		for col := 0; col < 5; col++ {
			bits := g[col]
			for row := 0; row < 7; row++ {
				if bits&(1<<uint(row)) != 0 {
					f.Set(x+col*2, y+row*2, true)
					f.Set(x+col*2+1, y+row*2, true)
					f.Set(x+col*2, y+row*2+1, true)
					f.Set(x+col*2+1, y+row*2+1, true)
				}
			}
		}
		x += 12
	}
	return x
}

// Shift copies the frame one pixel right, wrapping — cheap burn-in relief.
func (f *Frame) Shift(dx int) {
	if dx == 0 {
		return
	}
	dx = dx % width
	if dx < 0 {
		dx += width
	}
	var out Frame
	for p := 0; p < pages; p++ {
		for x := 0; x < width; x++ {
			out.Pages[p][(x+dx)%width] = f.Pages[p][x]
		}
	}
	*f = out
}

// Dump returns an ASCII preview ('.' / '#') for tests.
func (f Frame) Dump() string {
	var b strings.Builder
	b.Grow(65 * 64)
	for y := 0; y < 64; y++ {
		for x := 0; x < width; x++ {
			p, bit := y/8, byte(1)<<uint(y%8)
			if f.Pages[p][x]&bit != 0 {
				b.WriteByte('#')
			} else {
				b.WriteByte('.')
			}
		}
		b.WriteByte('\n')
	}
	return b.String()
}

// RowHasPixels reports whether any pixel in [y0,y1] is set.
func (f Frame) RowHasPixels(y0, y1 int) bool {
	for y := y0; y <= y1; y++ {
		for x := 0; x < width; x++ {
			p, bit := y/8, byte(1)<<uint(y%8)
			if f.Pages[p][x]&bit != 0 {
				return true
			}
		}
	}
	return false
}
