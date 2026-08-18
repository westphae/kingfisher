// Package wire is the Go side of the kingfisher pod wire format. It mirrors
// the Rust crate at `pod_wire/` and decodes postcard-encoded frames wrapped
// in a (u16 length, body, u32 crc32) envelope. Variant discriminants and
// field order MUST match the Rust enum/struct definitions exactly.
package wire

const (
	ProtoVersion     = 5
	MaxDeviceNameLen = 12
	MaxReadings      = 8
	MaxSensors       = 4
	HeaderLen        = 2
	CRCLen           = 4
	FramingOverhead  = HeaderLen + CRCLen
)

// Frame discriminants (postcard varint enum tag).
const (
	frameHello  byte = 0
	frameStatus byte = 1
	frameSample byte = 2
	frameCmd    byte = 3
	frameAck    byte = 4
	framePing   byte = 5
	framePong   byte = 6
)

// Cmd discriminants (inside a Frame::Cmd).
const (
	cmdSetRate byte = 0
	cmdSetAttr byte = 1
)

// Reading discriminants (inside a SampleBatch.samples item).
const (
	readingAirspeed byte = 0
	readingStatic   byte = 1
	readingMag      byte = 2
	readingBattery  byte = 3
)

// Frame is the top-level message envelope.
type Frame interface {
	isFrame()
}

// Hello is sent by the pod on (re-)connect.
type Hello struct {
	FwVersion    uint32
	ProtoVersion uint8
	Caps         Capabilities
}

func (Hello) isFrame() {}

type Capabilities struct {
	Sensors []SensorCap
}

// DeviceName is a fixed-width UTF-8 name on the wire (zero-padded).
type DeviceName [MaxDeviceNameLen]byte

func NewDeviceName(s string) DeviceName {
	var n DeviceName
	copy(n[:], s)
	return n
}

func (n DeviceName) String() string {
	for i, b := range n {
		if b == 0 {
			return string(n[:i])
		}
	}
	return string(n[:])
}

type SensorCap struct {
	ID         SensorID
	MinHz      uint16
	MaxHz      uint16
	DefaultHz  uint16
	DeviceName DeviceName
}

// SensorID enumerates the pod's sensors. Values match the Rust enum.
type SensorID uint8

const (
	SensorAirspeed SensorID = 0
	SensorStatic   SensorID = 1
	SensorMag      SensorID = 2
	SensorBattery  SensorID = 3
)

func (s SensorID) String() string {
	switch s {
	case SensorAirspeed:
		return "airspeed"
	case SensorStatic:
		return "static"
	case SensorMag:
		return "mag"
	case SensorBattery:
		return "battery"
	default:
		return "unknown"
	}
}

// Status is a periodic health frame from the pod.
type Status struct {
	PodUptimeUs     uint64
	BatteryV        float32
	RssiDBm         int8
	TxSeq           uint32
	RxSeqLast       uint32
	PowerMode       uint8
	SleepReason     uint8
	BufferDepth     uint16
	DroppedReadings uint32
}

func (Status) isFrame() {}

// SampleBatch carries one or more sensor readings sharing a pod timestamp.
type SampleBatch struct {
	PodUptimeUs uint64
	Seq         uint32
	Samples     []Reading
}

func (SampleBatch) isFrame() {}

// Reading is one sensor sample inside a SampleBatch.
type Reading interface {
	isReading()
	// AgeMicros is the per-reading delta from PodUptimeUs back to the
	// instant of the I/O read, in microseconds.
	AgeMicros() uint32
}

type AirspeedReading struct {
	DpPa  float32
	TempC float32
	AgeUs uint32
}

func (AirspeedReading) isReading()          {}
func (r AirspeedReading) AgeMicros() uint32 { return r.AgeUs }

type StaticReading struct {
	PPa   float32
	TempC float32
	AgeUs uint32
}

func (StaticReading) isReading()          {}
func (r StaticReading) AgeMicros() uint32 { return r.AgeUs }

type MagReading struct {
	XUt   float32
	YUt   float32
	ZUt   float32
	AgeUs uint32
}

func (MagReading) isReading()          {}
func (r MagReading) AgeMicros() uint32 { return r.AgeUs }

type BatteryReading struct {
	VoltageV          float32
	CurrentA          float32
	PowerW            float32
	CapacityRemainMah float32
	CapacityFullMah   float32
	SocPct            float32
	TimeRemainS       float32
	DesignCapacityMah uint16 // data-memory 0x3C
	AgeUs             uint32
}

func (BatteryReading) isReading()          {}
func (r BatteryReading) AgeMicros() uint32 { return r.AgeUs }

// Cmd is the Pi -> pod control message. It is wrapped in CmdFrame for
// transmission as a Frame::Cmd variant.
type Cmd interface {
	isCmd()
}

type CmdFrame struct {
	Seq uint32 // Pi-assigned; echoed in Ack.for_seq
	Cmd Cmd
}

func (CmdFrame) isFrame() {}

type CmdSetRate struct {
	Sensor SensorID
	Hz     uint16
}

func (CmdSetRate) isCmd() {}

type CmdSetAttr struct {
	Sensor SensorID
	Key    AttrKey
	Value  float32
}

func (CmdSetAttr) isCmd() {}

// AttrKey enumerates per-sensor tunables. Values match the Rust enum.
type AttrKey uint8

const (
	AttrDesignCapacity AttrKey = 0
	AttrBmpOsrPress    AttrKey = 1
	AttrBmpOsrTemp     AttrKey = 2
	AttrBmpIirPress    AttrKey = 3
	AttrBmpIirTemp     AttrKey = 4
	AttrMmcBandwidth   AttrKey = 5
	AttrQmaxCapacity   AttrKey = 6
)

// Ack acknowledges a Cmd by its outbound seq number.
type Ack struct {
	ForSeq uint32
	OK     bool
}

func (Ack) isFrame() {}

// Ping is the RTT/time-sync request. Pong echoes the original uptime.
type Ping struct {
	Seq            uint32
	SenderUptimeUs uint64
}

func (Ping) isFrame() {}

type Pong struct {
	Seq            uint32
	SenderUptimeUs uint64
	EchoUptimeUs   uint64
}

func (Pong) isFrame() {}
