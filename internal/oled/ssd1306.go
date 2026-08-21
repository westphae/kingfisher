package oled

import (
	"fmt"
	"log"
)

const (
	ctrlCmd  = 0x00
	ctrlData = 0x40
	width    = 128
	pages    = 8
)

// Display is a userspace SSD1306 (128×64) on /dev/i2c-N.
type Display struct {
	bus      *i2cDev
	colOff   int
	last     [pages][width]byte
	haveLast bool
}

// Open probes the bus and initializes the panel. columnOff is 0 for SSD1306
// and typically 2 for SH1106 modules that shift the picture.
func Open(busPath string, addr uint16, contrast byte, invert bool, columnOff int) (*Display, error) {
	if addr == 0x68 {
		return nil, fmt.Errorf("oled: refusing IMU address 0x68")
	}
	bus, err := openI2C(busPath, addr)
	if err != nil {
		return nil, err
	}
	d := &Display{bus: bus, colOff: columnOff}
	if err := d.init(contrast, invert); err != nil {
		_ = bus.Close()
		return nil, err
	}
	log.Printf("oled: SSD1306 on %s @ 0x%02x contrast=%d invert=%v col_off=%d",
		busPath, addr, contrast, invert, columnOff)
	return d, nil
}

func (d *Display) Close() error {
	if d == nil {
		return nil
	}
	if d.bus != nil {
		_ = d.cmd(0xAE) // display off
		return d.bus.Close()
	}
	return nil
}

func (d *Display) cmd(bytes ...byte) error {
	return d.bus.write(ctrlCmd, bytes)
}

func (d *Display) init(contrast byte, invert bool) error {
	seq := []byte{
		0xAE,       // display off
		0xD5, 0x80, // clock
		0xA8, 0x3F, // multiplex 64
		0xD3, 0x00, // offset
		0x40,       // start line
		0x8D, 0x14, // charge pump
		0x20, 0x02, // page addressing
		0xA1,       // segment remap
		0xC8,       // COM scan dec
		0xDA, 0x12, // COM pins
		0x81, contrast,
		0xD9, 0xF1, // precharge
		0xDB, 0x40, // VCOM
		0xA4, // resume RAM
	}
	if invert {
		seq = append(seq, 0xA7)
	} else {
		seq = append(seq, 0xA6)
	}
	seq = append(seq, 0xAF) // display on
	return d.cmd(seq...)
}

// SetInvert switches 0xA6/0xA7 without a full re-init.
func (d *Display) SetInvert(on bool) error {
	if on {
		return d.cmd(0xA7)
	}
	return d.cmd(0xA6)
}

// SetContrast programs 0x81.
func (d *Display) SetContrast(c byte) error {
	return d.cmd(0x81, c)
}

// Draw sends only pages that differ from the last frame.
func (d *Display) Draw(f Frame) error {
	for p := 0; p < pages; p++ {
		if d.haveLast && f.Pages[p] == d.last[p] {
			continue
		}
		col := d.colOff
		if err := d.cmd(
			byte(0xB0|p),
			byte(0x00|(col&0x0F)),
			byte(0x10|((col>>4)&0x0F)),
		); err != nil {
			return err
		}
		if err := d.bus.write(ctrlData, f.Pages[p][:]); err != nil {
			return err
		}
		d.last[p] = f.Pages[p]
	}
	d.haveLast = true
	return nil
}

// Invalidate forces the next Draw to rewrite every page.
func (d *Display) Invalidate() {
	d.haveLast = false
}
