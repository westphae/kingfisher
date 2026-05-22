package wire

import (
	"bufio"
	"encoding/hex"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// TestRustFixtures decodes the hex blobs produced by `pod_wire_dump`
// (in pod_wire/examples/) and checks they match Go-side expected values.
// Run `cargo run --example pod_wire_dump > internal/pod/wire/testdata/fixtures.txt`
// after changing the Rust wire types to regenerate.
func TestRustFixtures(t *testing.T) {
	want := map[string]Frame{
		"hello": Hello{
			FwVersion:    0x00010203,
			ProtoVersion: ProtoVersion,
			Caps: Capabilities{Sensors: []SensorCap{
				{ID: SensorAirspeed, MinHz: 1, MaxHz: 50, DefaultHz: 10},
				{ID: SensorMag, MinHz: 1, MaxHz: 200, DefaultHz: 50},
			}},
		},
		"sample_batch": SampleBatch{
			PodUptimeUs: 1_234_567_890,
			Seq:         42,
			Samples: []Reading{
				AirspeedReading{DpPa: 102.5, TempC: 18.3, AgeUs: 250},
				StaticReading{PPa: 98_765.0, TempC: 18.4, AgeUs: 100},
				MagReading{XUt: 21.3, YUt: -4.1, ZUt: 42.8, AgeUs: 0},
			},
		},
		"cmd_set_rate": CmdFrame{Seq: 1, Cmd: CmdSetRate{Sensor: SensorMag, Hz: 50}},
		"cmd_set_attr": CmdFrame{Seq: 2, Cmd: CmdSetAttr{
			Sensor: SensorStatic, Key: AttrOversampling, Value: 16.0,
		}},
		"cmd_ping":   CmdFrame{Seq: 3, Cmd: CmdPing{}},
		"cmd_reboot": CmdFrame{Seq: 4, Cmd: CmdReboot{}},
		"status": Status{
			PodUptimeUs: 5_000_000,
			BatteryV:    3.78,
			RssiDBm:     -64,
			TxSeq:       100,
			RxSeqLast:   12,
		},
		"ping":     Ping{Seq: 7, SenderUptimeUs: 999},
		"pong":     Pong{Seq: 7, SenderUptimeUs: 1100, EchoUptimeUs: 999},
		"ack_ok":   Ack{ForSeq: 99, OK: true},
		"ack_fail": Ack{ForSeq: 100, OK: false},
	}

	path := filepath.Join("testdata", "fixtures.txt")
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	defer f.Close()

	seen := map[string]bool{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "\t", 2)
		if len(parts) != 2 {
			t.Fatalf("bad fixture line: %q", line)
		}
		name, hexBlob := parts[0], parts[1]
		bytes, err := hex.DecodeString(hexBlob)
		if err != nil {
			t.Fatalf("%s: hex decode: %v", name, err)
		}
		got, err := Decode(bytes)
		if err != nil {
			t.Fatalf("%s: Decode: %v", name, err)
		}
		w, ok := want[name]
		if !ok {
			t.Errorf("%s: no expected value in test table", name)
			continue
		}
		if !reflect.DeepEqual(got, w) {
			t.Errorf("%s: decoded\n  got  = %#v\n  want = %#v", name, got, w)
		}
		seen[name] = true
	}
	if err := sc.Err(); err != nil {
		t.Fatalf("scan: %v", err)
	}
	for name := range want {
		if !seen[name] {
			t.Errorf("fixture %q not present in fixtures.txt", name)
		}
	}
}

// TestRoundTrip ensures Encode/Decode round-trips every Frame variant
// without needing the Rust dump output.
func TestRoundTrip(t *testing.T) {
	frames := []Frame{
		Hello{
			FwVersion: 7, ProtoVersion: ProtoVersion,
			Caps: Capabilities{Sensors: []SensorCap{
				{ID: SensorAirspeed, MinHz: 1, MaxHz: 50, DefaultHz: 10},
			}},
		},
		Status{PodUptimeUs: 99, BatteryV: 4.10, RssiDBm: -42, TxSeq: 1, RxSeqLast: 0},
		SampleBatch{PodUptimeUs: 12345, Seq: 1, Samples: []Reading{
			MagReading{XUt: 1, YUt: 2, ZUt: 3, AgeUs: 10},
		}},
		CmdFrame{Seq: 10, Cmd: CmdSetRate{Sensor: SensorMag, Hz: 100}},
		CmdFrame{Seq: 11, Cmd: CmdReboot{}},
		Ack{ForSeq: 5, OK: true},
		Ping{Seq: 1, SenderUptimeUs: 100},
		Pong{Seq: 1, SenderUptimeUs: 200, EchoUptimeUs: 100},
	}
	buf := make([]byte, 512)
	for _, f := range frames {
		n, err := Encode(f, buf)
		if err != nil {
			t.Fatalf("Encode %T: %v", f, err)
		}
		got, err := Decode(buf[:n])
		if err != nil {
			t.Fatalf("Decode %T: %v", f, err)
		}
		if !reflect.DeepEqual(got, f) {
			t.Errorf("%T round-trip mismatch:\n  got  = %#v\n  want = %#v", f, got, f)
		}
	}
}

func TestCRCRejection(t *testing.T) {
	buf := make([]byte, 64)
	n, err := Encode(CmdFrame{Cmd: CmdPing{}}, buf)
	if err != nil {
		t.Fatal(err)
	}
	buf[HeaderLen] ^= 0x01
	if _, err := Decode(buf[:n]); err != ErrCRC {
		t.Fatalf("expected ErrCRC, got %v", err)
	}
}
