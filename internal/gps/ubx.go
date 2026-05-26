package gps

import (
	"bufio"
	"fmt"
	"io"
	"net"
	"time"
)

const (
	ubxSync1      = 0xB5
	ubxSync2      = 0x62
	ubxClassNAV   = 0x01
	ubxIDNAVPVT   = 0x07
	ubxNavPVTNumSV = 23
)

// feedUBX scans buf for UBX frames and returns the number of bytes consumed
// (including any leading garbage before the first sync). onNumSV is called
// with the numSV byte from each NAV-PVT payload.
func feedUBX(buf []byte, onNumSV func(int)) int {
	i := 0
	for i+8 <= len(buf) {
		if buf[i] != ubxSync1 || buf[i+1] != ubxSync2 {
			i++
			continue
		}
		if i+6 > len(buf) {
			break
		}
		classID := buf[i+2]
		msgID := buf[i+3]
		length := int(buf[i+4]) | int(buf[i+5])<<8
		frameLen := 6 + length + 2
		if i+frameLen > len(buf) {
			break
		}
		if classID == ubxClassNAV && msgID == ubxIDNAVPVT && length > ubxNavPVTNumSV {
			onNumSV(int(buf[i+6+ubxNavPVTNumSV]))
		}
		i += frameLen
	}
	return i
}

func readUBXStream(r io.Reader, onNumSV func(int)) error {
	var carry []byte
	buf := make([]byte, 4096)
	for {
		n, err := r.Read(buf)
		if n > 0 {
			carry = append(carry, buf[:n]...)
			consumed := feedUBX(carry, onNumSV)
			if consumed > 0 {
				copy(carry, carry[consumed:])
				carry = carry[:len(carry)-consumed]
			}
			if len(carry) > 8192 {
				// Resync if we never find a valid frame.
				carry = carry[len(carry)-256:]
			}
		}
		if err != nil {
			return err
		}
	}
}

// pollUBXNumSV dials gpsd briefly, reads until the first NAV-PVT numSV byte,
// then closes. This avoids a permanent second watcher; it does not touch the
// serial GPS link (only tcp://gpsd), so the receiver's 10 Hz NAV-PVT stream
// is unchanged.
func pollUBXNumSV(addr string, limit time.Duration) (int, bool) {
	conn, err := net.DialTimeout("tcp4", addr, 2*time.Second)
	if err != nil {
		return 0, false
	}
	defer conn.Close()

	if _, err := fmt.Fprintf(conn, "?WATCH={\"enable\":true,\"json\":false,\"raw\":2}\n"); err != nil {
		return 0, false
	}

	br := bufio.NewReader(conn)
	deadline := time.Now().Add(limit)
	var carry []byte
	var nsv int
	got := false
	buf := make([]byte, 1024)

	for !got && time.Now().Before(deadline) {
		_ = conn.SetReadDeadline(deadline)
		b, err := br.ReadByte()
		if err != nil {
			break
		}
		if b == '{' {
			if _, err := br.ReadBytes('\n'); err != nil {
				break
			}
			continue
		}
		carry = append(carry, b)
		for !got {
			consumed := feedUBX(carry, func(n int) {
				nsv = n
				got = true
			})
			if consumed > 0 {
				copy(carry, carry[consumed:])
				carry = carry[:len(carry)-consumed]
			}
			if got {
				break
			}
			n, err := br.Read(buf)
			if n > 0 {
				carry = append(carry, buf[:n]...)
			}
			if err != nil {
				break
			}
			if len(carry) > 16384 {
				carry = nil
			}
		}
	}
	return nsv, got
}
