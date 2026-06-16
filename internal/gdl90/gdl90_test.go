package gdl90

import (
	"bytes"
	"net"
	"os"
	"testing"
	"time"
)

func TestCRCComputeKnown(t *testing.T) {
	msg := []byte{0x00, 0x11, 0x81, 0x00, 0x00, 0x00, 0x00}
	crc := crcCompute(msg)
	if crc == 0 {
		t.Fatal("crc should be non-zero")
	}
}

func TestPrepareMessageFlags(t *testing.T) {
	raw := []byte{0x00, 0x01}
	framed := PrepareMessage(raw)
	if framed[0] != 0x7E || framed[len(framed)-1] != 0x7E {
		t.Fatalf("expected 0x7E flags, got % x", framed)
	}
	if len(framed) < len(raw)+4 {
		t.Fatalf("frame too short: % x", framed)
	}
}

func TestPrepareMessageByteStuffing(t *testing.T) {
	raw := []byte{0x4C, 0x7E, 0x7D, 0x01}
	framed := PrepareMessage(raw)
	inner := framed[1 : len(framed)-1]
	if !bytes.Contains(inner, []byte{0x7D, 0x5E}) {
		t.Fatalf("expected stuffed 0x7E, got % x", inner)
	}
	if !bytes.Contains(inner, []byte{0x7D, 0x5D}) {
		t.Fatalf("expected stuffed 0x7D, got % x", inner)
	}
}

func TestEncodeLatLng(t *testing.T) {
	b := EncodeLatLng(37.0)
	if b[0] == 0 && b[1] == 0 && b[2] == 0 {
		t.Fatalf("unexpected zero lat encoding for 37°")
	}
	b2 := EncodeLatLng(-122.0)
	if b2[0] == 0 && b2[1] == 0 && b2[2] == 0 {
		t.Fatalf("unexpected zero lng encoding for -122°")
	}
}

func TestHeartbeatMessageID(t *testing.T) {
	framed := Heartbeat(true)
	if len(framed) < 3 || framed[0] != 0x7E {
		t.Fatalf("bad frame: % x", framed)
	}
	if framed[1] != 0x00 {
		t.Fatalf("expected message id 0x00, got 0x%02x", framed[1])
	}
}

func TestStratuxHeartbeatGPSAHRS(t *testing.T) {
	framed := StratuxHeartbeat(true, true)
	if framed[1] != 0xCC {
		t.Fatalf("expected 0xCC, got 0x%02x", framed[1])
	}
}

func TestOwnshipRequiresGPS(t *testing.T) {
	if Ownship(Situation{GPSValid: false}) != nil {
		t.Fatal("ownship should be nil without GPS")
	}
	s := Situation{
		GPSValid:       true,
		Lat:            37.5,
		Lon:            -122.0,
		GroundSpeedMps: 10,
		TrackDeg:       90,
		BaroValid:      true,
		PressureAltFt:  1500,
		Callsign:       "N12345",
	}
	framed := Ownship(s)
	if framed == nil || framed[0] != 0x7E {
		t.Fatalf("bad ownship frame: % x", framed)
	}
}

func TestAHRSReportMessageHeader(t *testing.T) {
	s := Situation{
		AHRSValid:  true,
		RollDeg:    -2.0,
		PitchDeg:   -1.0,
		HeadingDeg: 180.0,
	}
	framed := AHRSReport(s)
	if len(framed) < 4 {
		t.Fatal("frame too short")
	}
	if framed[1] != 0x4C {
		t.Fatalf("expected 0x4C, got 0x%02x", framed[1])
	}
}

func TestParseDHCPLeases(t *testing.T) {
	content := `lease 192.168.10.42 {
  starts 1 2024/01/01 00:00:00;
  ends 1 2024/01/02 00:00:00;
  client-hostname "ipad";
}
lease 192.168.10.43 {
  starts 1 2024/01/01 00:00:00;
  ends 1 2024/01/02 00:00:00;
}
`
	path := t.TempDir() + "/dhcpd.leases"
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	m, err := parseDHCPLeases(path)
	if err != nil {
		t.Fatal(err)
	}
	if m["192.168.10.42"] != "ipad" {
		t.Fatalf("hostname: %q", m["192.168.10.42"])
	}
	if _, ok := m["192.168.10.43"]; !ok {
		t.Fatal("missing lease without hostname")
	}
}

func TestClientPoolStaticIP(t *testing.T) {
	pool := NewClientPool(0, "", []string{"127.0.0.1"})
	pool.Refresh()
	if pool.Count() != 1 {
		t.Fatalf("expected 1 client, got %d", pool.Count())
	}
	n := pool.Send(Heartbeat(false))
	if n != 1 {
		t.Fatalf("send count %d", n)
	}
	pool.Close()
}

func TestUDPBroadcastIntegration(t *testing.T) {
	conn, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	port := conn.LocalAddr().(*net.UDPAddr).Port

	pool := NewClientPool(port, "", []string{"127.0.0.1"})
	pool.Refresh()
	defer pool.Close()

	done := make(chan []byte, 1)
	go func() {
		buf := make([]byte, 256)
		_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
		n, _, err := conn.ReadFromUDP(buf)
		if err != nil {
			return
		}
		done <- append([]byte(nil), buf[:n]...)
	}()

	pool.Send(Heartbeat(true))
	select {
	case pkt := <-done:
		if len(pkt) < 2 || pkt[0] != 0x7E {
			t.Fatalf("bad packet: % x", pkt)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("timed out waiting for UDP packet")
	}
}
