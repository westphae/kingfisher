package oled

import (
	"fmt"
	"os"
	"runtime"
	"unsafe"

	"golang.org/x/sys/unix"
)

const (
	i2cRdwr = 0x0707
	i2cMRd  = 0x0001
)

type i2cMsg struct {
	addr  uint16
	flags uint16
	len   uint16
	buf   uintptr
}

type i2cRdwrData struct {
	msgs  uintptr
	nmsgs uint32
}

type i2cDev struct {
	f    *os.File
	addr uint16
}

func openI2C(path string, addr uint16) (*i2cDev, error) {
	f, err := os.OpenFile(path, os.O_RDWR, 0)
	if err != nil {
		return nil, fmt.Errorf("oled: open %s: %w", path, err)
	}
	return &i2cDev{f: f, addr: addr}, nil
}

func (d *i2cDev) Close() error {
	if d == nil || d.f == nil {
		return nil
	}
	return d.f.Close()
}

func (d *i2cDev) write(ctrl byte, payload []byte) error {
	buf := make([]byte, 1+len(payload))
	buf[0] = ctrl
	copy(buf[1:], payload)
	msg := i2cMsg{addr: d.addr, flags: 0, len: uint16(len(buf)), buf: uintptr(unsafe.Pointer(&buf[0]))}
	data := i2cRdwrData{msgs: uintptr(unsafe.Pointer(&msg)), nmsgs: 1}
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, d.f.Fd(), i2cRdwr, uintptr(unsafe.Pointer(&data)))
	runtime.KeepAlive(&buf)
	runtime.KeepAlive(&msg)
	if errno != 0 {
		return fmt.Errorf("oled: i2c write: %w", errno)
	}
	return nil
}
