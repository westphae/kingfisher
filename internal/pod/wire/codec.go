package wire

import (
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"math"
)

var (
	ErrShort          = errors.New("wire: input shorter than framing overhead")
	ErrLengthBounds   = errors.New("wire: declared length exceeds buffer")
	ErrCRC            = errors.New("wire: crc mismatch")
	ErrVarintTooLong  = errors.New("wire: varint exceeds 10 bytes")
	ErrUnknownVariant = errors.New("wire: unknown enum discriminant")
	ErrTrailingBytes  = errors.New("wire: trailing bytes after frame body")
)

// Decode parses one framed datagram. The CRC is validated before the body
// is interpreted; corrupted frames return ErrCRC and never reach the
// postcard layer.
func Decode(buf []byte) (Frame, error) {
	if len(buf) < FramingOverhead {
		return nil, ErrShort
	}
	bodyLen := int(binary.LittleEndian.Uint16(buf[:HeaderLen]))
	if len(buf) < HeaderLen+bodyLen+CRCLen {
		return nil, ErrLengthBounds
	}
	body := buf[HeaderLen : HeaderLen+bodyLen]
	crcGot := binary.LittleEndian.Uint32(buf[HeaderLen+bodyLen : HeaderLen+bodyLen+CRCLen])
	if crcGot != crc32.ChecksumIEEE(body) {
		return nil, ErrCRC
	}
	d := newDecoder(body)
	frame, err := d.decodeFrame()
	if err != nil {
		return nil, err
	}
	if !d.empty() {
		return nil, ErrTrailingBytes
	}
	return frame, nil
}

// Encode writes one framed datagram to out (which must be large enough).
// Returns the number of bytes written.
func Encode(frame Frame, out []byte) (int, error) {
	if len(out) < FramingOverhead {
		return 0, ErrShort
	}
	e := newEncoder(out[HeaderLen : len(out)-CRCLen])
	if err := e.encodeFrame(frame); err != nil {
		return 0, err
	}
	bodyLen := e.pos
	binary.LittleEndian.PutUint16(out[:HeaderLen], uint16(bodyLen))
	crc := crc32.ChecksumIEEE(out[HeaderLen : HeaderLen+bodyLen])
	binary.LittleEndian.PutUint32(out[HeaderLen+bodyLen:HeaderLen+bodyLen+CRCLen], crc)
	return HeaderLen + bodyLen + CRCLen, nil
}

// --------------------------------------------------------------------------
// Decoder

type decoder struct {
	buf []byte
	pos int
}

func newDecoder(b []byte) *decoder { return &decoder{buf: b} }

func (d *decoder) empty() bool { return d.pos >= len(d.buf) }

func (d *decoder) byte() (byte, error) {
	if d.pos >= len(d.buf) {
		return 0, ErrShort
	}
	b := d.buf[d.pos]
	d.pos++
	return b, nil
}

// uvarint decodes a postcard varint (LEB128: 7-bit chunks, MSB continuation).
func (d *decoder) uvarint() (uint64, error) {
	var result uint64
	var shift uint
	for i := 0; i < 10; i++ {
		b, err := d.byte()
		if err != nil {
			return 0, err
		}
		result |= uint64(b&0x7f) << shift
		if b&0x80 == 0 {
			return result, nil
		}
		shift += 7
	}
	return 0, ErrVarintTooLong
}

func (d *decoder) f32() (float32, error) {
	if d.pos+4 > len(d.buf) {
		return 0, ErrShort
	}
	bits := binary.LittleEndian.Uint32(d.buf[d.pos:])
	d.pos += 4
	return math.Float32frombits(bits), nil
}

func (d *decoder) bool() (bool, error) {
	b, err := d.byte()
	if err != nil {
		return false, err
	}
	return b != 0, nil
}

func (d *decoder) decodeFrame() (Frame, error) {
	disc, err := d.uvarint()
	if err != nil {
		return nil, err
	}
	switch byte(disc) {
	case frameHello:
		return d.decodeHello()
	case frameStatus:
		return d.decodeStatus()
	case frameSample:
		return d.decodeSample()
	case frameCmd:
		seq, err := d.uvarint()
		if err != nil {
			return nil, err
		}
		c, err := d.decodeCmd()
		if err != nil {
			return nil, err
		}
		return CmdFrame{Seq: uint32(seq), Cmd: c}, nil
	case frameAck:
		return d.decodeAck()
	case framePing:
		return d.decodePing()
	case framePong:
		return d.decodePong()
	default:
		return nil, fmt.Errorf("%w: frame=%d", ErrUnknownVariant, disc)
	}
}

func (d *decoder) decodeHello() (Hello, error) {
	fw, err := d.uvarint()
	if err != nil {
		return Hello{}, err
	}
	pv, err := d.byte()
	if err != nil {
		return Hello{}, err
	}
	n, err := d.uvarint()
	if err != nil {
		return Hello{}, err
	}
	sensors := make([]SensorCap, 0, n)
	for i := uint64(0); i < n; i++ {
		id, err := d.byte()
		if err != nil {
			return Hello{}, err
		}
		minHz, err := d.uvarint()
		if err != nil {
			return Hello{}, err
		}
		maxHz, err := d.uvarint()
		if err != nil {
			return Hello{}, err
		}
		def, err := d.uvarint()
		if err != nil {
			return Hello{}, err
		}
		cap := SensorCap{
			ID:        SensorID(id),
			MinHz:     uint16(minHz),
			MaxHz:     uint16(maxHz),
			DefaultHz: uint16(def),
		}
		if pv >= 2 {
			for j := 0; j < MaxDeviceNameLen; j++ {
				b, err := d.byte()
				if err != nil {
					return Hello{}, err
				}
				cap.DeviceName[j] = b
			}
		}
		if cap.DeviceName.String() == "" {
			cap.DeviceName = defaultCapDeviceName(cap.ID)
		}
		sensors = append(sensors, cap)
	}
	return Hello{
		FwVersion:    uint32(fw),
		ProtoVersion: pv,
		Caps:         Capabilities{Sensors: sensors},
	}, nil
}

func (d *decoder) decodeStatus() (Status, error) {
	uptime, err := d.uvarint()
	if err != nil {
		return Status{}, err
	}
	batt, err := d.f32()
	if err != nil {
		return Status{}, err
	}
	rssi, err := d.byte()
	if err != nil {
		return Status{}, err
	}
	tx, err := d.uvarint()
	if err != nil {
		return Status{}, err
	}
	rx, err := d.uvarint()
	if err != nil {
		return Status{}, err
	}
	st := Status{
		PodUptimeUs: uptime,
		BatteryV:    batt,
		RssiDBm:     int8(rssi),
		TxSeq:       uint32(tx),
		RxSeqLast:   uint32(rx),
	}
	// Pre–deep-sleep firmware omitted power/buffer tail fields; accept short bodies.
	if d.empty() {
		return st, nil
	}
	mode, err := d.byte()
	if err != nil {
		return Status{}, err
	}
	reason, err := d.byte()
	if err != nil {
		return Status{}, err
	}
	depth, err := d.uvarint()
	if err != nil {
		return Status{}, err
	}
	dropped, err := d.uvarint()
	if err != nil {
		return Status{}, err
	}
	st.PowerMode = mode
	st.SleepReason = reason
	st.BufferDepth = uint16(depth)
	st.DroppedReadings = uint32(dropped)
	return st, nil
}

func (d *decoder) decodeSample() (SampleBatch, error) {
	uptime, err := d.uvarint()
	if err != nil {
		return SampleBatch{}, err
	}
	seq, err := d.uvarint()
	if err != nil {
		return SampleBatch{}, err
	}
	n, err := d.uvarint()
	if err != nil {
		return SampleBatch{}, err
	}
	if n > MaxReadings {
		return SampleBatch{}, fmt.Errorf("wire: sample batch count=%d exceeds MaxReadings", n)
	}
	samples := make([]Reading, 0, n)
	for i := uint64(0); i < n; i++ {
		disc, err := d.uvarint()
		if err != nil {
			return SampleBatch{}, err
		}
		r, err := d.decodeReading(byte(disc))
		if err != nil {
			return SampleBatch{}, err
		}
		samples = append(samples, r)
	}
	return SampleBatch{
		PodUptimeUs: uptime,
		Seq:         uint32(seq),
		Samples:     samples,
	}, nil
}

func (d *decoder) decodeReading(disc byte) (Reading, error) {
	switch disc {
	case readingAirspeed:
		dp, err := d.f32()
		if err != nil {
			return nil, err
		}
		t, err := d.f32()
		if err != nil {
			return nil, err
		}
		age, err := d.uvarint()
		if err != nil {
			return nil, err
		}
		return AirspeedReading{DpPa: dp, TempC: t, AgeUs: uint32(age)}, nil
	case readingStatic:
		p, err := d.f32()
		if err != nil {
			return nil, err
		}
		t, err := d.f32()
		if err != nil {
			return nil, err
		}
		age, err := d.uvarint()
		if err != nil {
			return nil, err
		}
		return StaticReading{PPa: p, TempC: t, AgeUs: uint32(age)}, nil
	case readingMag:
		x, err := d.f32()
		if err != nil {
			return nil, err
		}
		y, err := d.f32()
		if err != nil {
			return nil, err
		}
		z, err := d.f32()
		if err != nil {
			return nil, err
		}
		age, err := d.uvarint()
		if err != nil {
			return nil, err
		}
		return MagReading{XUt: x, YUt: y, ZUt: z, AgeUs: uint32(age)}, nil
	case readingBattery:
		voltage, err := d.f32()
		if err != nil {
			return nil, err
		}
		current, err := d.f32()
		if err != nil {
			return nil, err
		}
		power, err := d.f32()
		if err != nil {
			return nil, err
		}
		capRemain, err := d.f32()
		if err != nil {
			return nil, err
		}
		capFull, err := d.f32()
		if err != nil {
			return nil, err
		}
		soc, err := d.f32()
		if err != nil {
			return nil, err
		}
		timeRemain, err := d.f32()
		if err != nil {
			return nil, err
		}
		designMah, err := d.uvarint()
		if err != nil {
			return nil, err
		}
		age, err := d.uvarint()
		if err != nil {
			return nil, err
		}
		return BatteryReading{
			VoltageV:          voltage,
			CurrentA:          current,
			PowerW:            power,
			CapacityRemainMah: capRemain,
			CapacityFullMah:   capFull,
			SocPct:            soc,
			TimeRemainS:       timeRemain,
			DesignCapacityMah: uint16(designMah),
			AgeUs:             uint32(age),
		}, nil
	default:
		return nil, fmt.Errorf("%w: reading=%d", ErrUnknownVariant, disc)
	}
}

func (d *decoder) decodeCmd() (Cmd, error) {
	disc, err := d.uvarint()
	if err != nil {
		return nil, err
	}
	switch byte(disc) {
	case cmdSetRate:
		id, err := d.byte()
		if err != nil {
			return nil, err
		}
		hz, err := d.uvarint()
		if err != nil {
			return nil, err
		}
		return CmdSetRate{Sensor: SensorID(id), Hz: uint16(hz)}, nil
	case cmdSetAttr:
		id, err := d.byte()
		if err != nil {
			return nil, err
		}
		key, err := d.byte()
		if err != nil {
			return nil, err
		}
		val, err := d.f32()
		if err != nil {
			return nil, err
		}
		return CmdSetAttr{Sensor: SensorID(id), Key: AttrKey(key), Value: val}, nil
	default:
		return nil, fmt.Errorf("%w: cmd=%d", ErrUnknownVariant, disc)
	}
}

func (d *decoder) decodeAck() (Ack, error) {
	seq, err := d.uvarint()
	if err != nil {
		return Ack{}, err
	}
	ok, err := d.bool()
	if err != nil {
		return Ack{}, err
	}
	return Ack{ForSeq: uint32(seq), OK: ok}, nil
}

func (d *decoder) decodePing() (Ping, error) {
	seq, err := d.uvarint()
	if err != nil {
		return Ping{}, err
	}
	uptime, err := d.uvarint()
	if err != nil {
		return Ping{}, err
	}
	return Ping{Seq: uint32(seq), SenderUptimeUs: uptime}, nil
}

func (d *decoder) decodePong() (Pong, error) {
	seq, err := d.uvarint()
	if err != nil {
		return Pong{}, err
	}
	uptime, err := d.uvarint()
	if err != nil {
		return Pong{}, err
	}
	echo, err := d.uvarint()
	if err != nil {
		return Pong{}, err
	}
	return Pong{Seq: uint32(seq), SenderUptimeUs: uptime, EchoUptimeUs: echo}, nil
}

// --------------------------------------------------------------------------
// Encoder

type encoder struct {
	buf []byte
	pos int
}

func newEncoder(b []byte) *encoder { return &encoder{buf: b} }

func (e *encoder) byte(b byte) error {
	if e.pos >= len(e.buf) {
		return ErrShort
	}
	e.buf[e.pos] = b
	e.pos++
	return nil
}

func (e *encoder) uvarint(v uint64) error {
	for v >= 0x80 {
		if err := e.byte(byte(v) | 0x80); err != nil {
			return err
		}
		v >>= 7
	}
	return e.byte(byte(v))
}

func (e *encoder) f32(v float32) error {
	if e.pos+4 > len(e.buf) {
		return ErrShort
	}
	binary.LittleEndian.PutUint32(e.buf[e.pos:], math.Float32bits(v))
	e.pos += 4
	return nil
}

func (e *encoder) bool(v bool) error {
	if v {
		return e.byte(1)
	}
	return e.byte(0)
}

func (e *encoder) encodeFrame(f Frame) error {
	switch v := f.(type) {
	case Hello:
		if err := e.uvarint(uint64(frameHello)); err != nil {
			return err
		}
		return e.encodeHello(v)
	case Status:
		if err := e.uvarint(uint64(frameStatus)); err != nil {
			return err
		}
		return e.encodeStatus(v)
	case SampleBatch:
		if err := e.uvarint(uint64(frameSample)); err != nil {
			return err
		}
		return e.encodeSample(v)
	case CmdFrame:
		if err := e.uvarint(uint64(frameCmd)); err != nil {
			return err
		}
		if err := e.uvarint(uint64(v.Seq)); err != nil {
			return err
		}
		return e.encodeCmd(v.Cmd)
	case Ack:
		if err := e.uvarint(uint64(frameAck)); err != nil {
			return err
		}
		if err := e.uvarint(uint64(v.ForSeq)); err != nil {
			return err
		}
		return e.bool(v.OK)
	case Ping:
		if err := e.uvarint(uint64(framePing)); err != nil {
			return err
		}
		if err := e.uvarint(uint64(v.Seq)); err != nil {
			return err
		}
		return e.uvarint(v.SenderUptimeUs)
	case Pong:
		if err := e.uvarint(uint64(framePong)); err != nil {
			return err
		}
		if err := e.uvarint(uint64(v.Seq)); err != nil {
			return err
		}
		if err := e.uvarint(v.SenderUptimeUs); err != nil {
			return err
		}
		return e.uvarint(v.EchoUptimeUs)
	default:
		return fmt.Errorf("wire: cannot encode frame of type %T", f)
	}
}

func (e *encoder) encodeHello(h Hello) error {
	if err := e.uvarint(uint64(h.FwVersion)); err != nil {
		return err
	}
	if err := e.byte(h.ProtoVersion); err != nil {
		return err
	}
	if err := e.uvarint(uint64(len(h.Caps.Sensors))); err != nil {
		return err
	}
	for _, s := range h.Caps.Sensors {
		if err := e.byte(byte(s.ID)); err != nil {
			return err
		}
		if err := e.uvarint(uint64(s.MinHz)); err != nil {
			return err
		}
		if err := e.uvarint(uint64(s.MaxHz)); err != nil {
			return err
		}
		if err := e.uvarint(uint64(s.DefaultHz)); err != nil {
			return err
		}
		name := s.DeviceName
		if name.String() == "" {
			name = defaultCapDeviceName(s.ID)
		}
		for _, b := range name {
			if err := e.byte(b); err != nil {
				return err
			}
		}
	}
	return nil
}

func defaultCapDeviceName(id SensorID) DeviceName {
	switch id {
	case SensorStatic:
		return NewDeviceName("bmp581")
	case SensorMag:
		return NewDeviceName("mmc5983")
	case SensorAirspeed:
		return NewDeviceName("ms4525")
	case SensorBattery:
		return NewDeviceName("bq27441")
	default:
		return DeviceName{}
	}
}

func (e *encoder) encodeStatus(s Status) error {
	if err := e.uvarint(s.PodUptimeUs); err != nil {
		return err
	}
	if err := e.f32(s.BatteryV); err != nil {
		return err
	}
	if err := e.byte(byte(s.RssiDBm)); err != nil {
		return err
	}
	if err := e.uvarint(uint64(s.TxSeq)); err != nil {
		return err
	}
	if err := e.uvarint(uint64(s.RxSeqLast)); err != nil {
		return err
	}
	if err := e.byte(s.PowerMode); err != nil {
		return err
	}
	if err := e.byte(s.SleepReason); err != nil {
		return err
	}
	if err := e.uvarint(uint64(s.BufferDepth)); err != nil {
		return err
	}
	return e.uvarint(uint64(s.DroppedReadings))
}

func (e *encoder) encodeSample(b SampleBatch) error {
	if err := e.uvarint(b.PodUptimeUs); err != nil {
		return err
	}
	if err := e.uvarint(uint64(b.Seq)); err != nil {
		return err
	}
	if err := e.uvarint(uint64(len(b.Samples))); err != nil {
		return err
	}
	for _, r := range b.Samples {
		if err := e.encodeReading(r); err != nil {
			return err
		}
	}
	return nil
}

func (e *encoder) encodeReading(r Reading) error {
	switch v := r.(type) {
	case AirspeedReading:
		if err := e.uvarint(uint64(readingAirspeed)); err != nil {
			return err
		}
		if err := e.f32(v.DpPa); err != nil {
			return err
		}
		if err := e.f32(v.TempC); err != nil {
			return err
		}
		return e.uvarint(uint64(v.AgeUs))
	case StaticReading:
		if err := e.uvarint(uint64(readingStatic)); err != nil {
			return err
		}
		if err := e.f32(v.PPa); err != nil {
			return err
		}
		if err := e.f32(v.TempC); err != nil {
			return err
		}
		return e.uvarint(uint64(v.AgeUs))
	case MagReading:
		if err := e.uvarint(uint64(readingMag)); err != nil {
			return err
		}
		if err := e.f32(v.XUt); err != nil {
			return err
		}
		if err := e.f32(v.YUt); err != nil {
			return err
		}
		if err := e.f32(v.ZUt); err != nil {
			return err
		}
		return e.uvarint(uint64(v.AgeUs))
	case BatteryReading:
		if err := e.uvarint(uint64(readingBattery)); err != nil {
			return err
		}
		if err := e.f32(v.VoltageV); err != nil {
			return err
		}
		if err := e.f32(v.CurrentA); err != nil {
			return err
		}
		if err := e.f32(v.PowerW); err != nil {
			return err
		}
		if err := e.f32(v.CapacityRemainMah); err != nil {
			return err
		}
		if err := e.f32(v.CapacityFullMah); err != nil {
			return err
		}
		if err := e.f32(v.SocPct); err != nil {
			return err
		}
		if err := e.f32(v.TimeRemainS); err != nil {
			return err
		}
		if err := e.uvarint(uint64(v.DesignCapacityMah)); err != nil {
			return err
		}
		return e.uvarint(uint64(v.AgeUs))
	default:
		return fmt.Errorf("wire: cannot encode reading of type %T", r)
	}
}

func (e *encoder) encodeCmd(c Cmd) error {
	switch v := c.(type) {
	case CmdSetRate:
		if err := e.uvarint(uint64(cmdSetRate)); err != nil {
			return err
		}
		if err := e.byte(byte(v.Sensor)); err != nil {
			return err
		}
		return e.uvarint(uint64(v.Hz))
	case CmdSetAttr:
		if err := e.uvarint(uint64(cmdSetAttr)); err != nil {
			return err
		}
		if err := e.byte(byte(v.Sensor)); err != nil {
			return err
		}
		if err := e.byte(byte(v.Key)); err != nil {
			return err
		}
		return e.f32(v.Value)
	default:
		return fmt.Errorf("wire: cannot encode cmd variant %T", c)
	}
}
