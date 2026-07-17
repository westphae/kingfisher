// Package ups reads the Geekworm X1200 UPS HAT: a MAX17040 fuel gauge on
// I²C bus 1 @0x36 (cell voltage + modeled SOC — no current sense) and a
// power-loss-detect line on GPIO6 (1 = external power present). It publishes
// the `ups` hub device and triggers the clean-shutdown path when battery
// exhaustion is imminent.
//
// Bus 1 is shared with the icm45686 IMU at 0x68 under a kernel driver; the
// kernel serializes per-transaction, so userspace access to 0x36 coexists —
// nothing here may ever address 0x68.
package ups

import (
	"fmt"
	"os"
	"runtime"
	"unsafe"

	"golang.org/x/sys/unix"
)

const (
	busPath   = "/dev/i2c-1"
	gaugeAddr = 0x36

	regVCell   = 0x02 // 12-bit cell voltage, 1.25 mV/LSB in the upper 12 bits
	regSOC     = 0x04 // SOC, 1/256 % per LSB
	regVersion = 0x08 // presence probe (reads 0x0002 on this board)

	i2cRdwr = 0x0707 // ioctl I2C_RDWR
	i2cMRd  = 0x0001 // i2c_msg flag: read
)

// Gauge is the MAX17040 read surface, an interface so the Monitor's decision
// logic is testable without the HAT.
type Gauge interface {
	// ReadVoltageSOC returns cell volts and state-of-charge percent.
	ReadVoltageSOC() (volts, socPct float64, err error)
	Version() (uint16, error)
	Close() error
}

// i2cMsg mirrors struct i2c_msg; the compiler pads len→buf to pointer
// alignment exactly as the kernel struct does.
type i2cMsg struct {
	addr  uint16
	flags uint16
	len   uint16
	buf   uintptr
}

// i2cRdwrData mirrors struct i2c_rdwr_ioctl_data.
type i2cRdwrData struct {
	msgs  uintptr
	nmsgs uint32
}

type max17040 struct {
	f *os.File
}

// openGauge opens the bus and probes the VERSION register so a missing HAT
// (or unloaded i2c-dev module) is detected at open, not on first sample.
func openGauge() (Gauge, error) {
	f, err := os.OpenFile(busPath, os.O_RDWR, 0)
	if err != nil {
		return nil, err
	}
	g := &max17040{f: f}
	if _, err := g.Version(); err != nil {
		f.Close()
		return nil, fmt.Errorf("max17040 probe @0x%02x: %w", gaugeAddr, err)
	}
	return g, nil
}

func (g *max17040) ReadVoltageSOC() (float64, float64, error) {
	rawV, err := g.readWord(regVCell)
	if err != nil {
		return 0, 0, err
	}
	rawS, err := g.readWord(regSOC)
	if err != nil {
		return 0, 0, err
	}
	return vcellVolts(rawV), socPct(rawS), nil
}

func (g *max17040) Version() (uint16, error) { return g.readWord(regVersion) }

func (g *max17040) Close() error { return g.f.Close() }

// readWord does one I2C_RDWR combined transaction (register write +
// repeated-start 2-byte read) — atomic on the shared bus.
func (g *max17040) readWord(reg byte) (uint16, error) {
	wbuf := [1]byte{reg}
	var rbuf [2]byte
	msgs := [2]i2cMsg{
		{addr: gaugeAddr, flags: 0, len: 1, buf: uintptr(unsafe.Pointer(&wbuf[0]))},
		{addr: gaugeAddr, flags: i2cMRd, len: 2, buf: uintptr(unsafe.Pointer(&rbuf[0]))},
	}
	data := i2cRdwrData{msgs: uintptr(unsafe.Pointer(&msgs[0])), nmsgs: 2}
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, g.f.Fd(), i2cRdwr, uintptr(unsafe.Pointer(&data)))
	runtime.KeepAlive(&wbuf)
	runtime.KeepAlive(&rbuf)
	runtime.KeepAlive(&msgs)
	if errno != 0 {
		return 0, fmt.Errorf("i2c reg 0x%02x: %w", reg, errno)
	}
	return wordFromBytes(rbuf[0], rbuf[1]), nil
}

// wordFromBytes assembles the register value MSB-first — the chip's wire
// order on a raw read (the byte swap seen with SMBus word reads is an
// artifact of that protocol, not of the chip).
func wordFromBytes(msb, lsb byte) uint16 {
	return uint16(msb)<<8 | uint16(lsb)
}

// vcellVolts converts a raw VCELL register word: 12-bit value in the upper
// bits, 1.25 mV/LSB.
func vcellVolts(raw uint16) float64 {
	return float64(raw) * 1.25 / 1000 / 16
}

// socPct converts a raw SOC register word: whole percent in the MSB,
// 1/256 % in the LSB.
func socPct(raw uint16) float64 {
	return float64(raw) / 256
}
