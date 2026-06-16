// Package gdl90 emits Stratux-compatible GDL90 UDP messages for EFB apps.
package gdl90

// Framing and CRC ported from Stratux gen_gdl90.go (BSD license).

const flagByte = 0x7E
const controlEscape = 0x7D

var crc16Table [256]uint16

func init() {
	var i, bitctr, crc uint16
	for i = 0; i < 256; i++ {
		crc = i << 8
		for bitctr = 0; bitctr < 8; bitctr++ {
			z := uint16(0)
			if crc&0x8000 != 0 {
				z = 0x1021
			}
			crc = (crc << 1) ^ z
		}
		crc16Table[i] = crc
	}
}

func crcCompute(data []byte) uint16 {
	ret := uint16(0)
	for i := range data {
		ret = crc16Table[ret>>8] ^ (ret << 8) ^ uint16(data[i])
	}
	return ret
}

// PrepareMessage appends CRC, byte-stuffs, and wraps payload in GDL90 flag bytes.
func PrepareMessage(data []byte) []byte {
	crc := crcCompute(data)
	payload := append(append([]byte(nil), data...), byte(crc&0xFF), byte(crc>>8))

	out := make([]byte, 0, len(payload)+4)
	out = append(out, flagByte)
	for _, mv := range payload {
		if mv == flagByte || mv == controlEscape {
			out = append(out, controlEscape, mv^0x20)
		} else {
			out = append(out, mv)
		}
	}
	out = append(out, flagByte)
	return out
}

// Pack is an alias for PrepareMessage.
func Pack(data []byte) []byte { return PrepareMessage(data) }
