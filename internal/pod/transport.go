package pod

import (
	"context"
	"fmt"
	"net"

	"github.com/westphae/kingfisher/internal/pod/wire"
)

// Transport is the byte-pipe abstraction between the Pi-side pod.Client and
// whatever physical link reaches the wing pod. v1 has only a UDP
// implementation; a UART-to-ESP-NOW-dongle implementation can drop in here
// later without touching the rest of internal/pod.
type Transport interface {
	// Send encodes and transmits a frame. Returns an error if the wire
	// envelope cannot be encoded or the underlying link rejects the write.
	Send(f wire.Frame) error
	// Recv blocks until a frame arrives or ctx is cancelled. Returns the
	// decoded frame and the sender's address as a human-readable string.
	// On CRC or decode failure, Recv must log internally and continue.
	Recv(ctx context.Context) (wire.Frame, string, error)
	// Close shuts the transport down; subsequent Send/Recv calls fail.
	Close() error
}

// UDP is the v1 transport. It listens on a single UDP port and remembers
// the last peer it heard from so Send can target the same pod.
type UDP struct {
	conn    *net.UDPConn
	peerCh  chan net.Addr
	lastPeer net.Addr
}

// ListenUDP binds the given UDP address and returns a ready Transport.
func ListenUDP(addr string) (*UDP, error) {
	udpAddr, err := net.ResolveUDPAddr("udp", addr)
	if err != nil {
		return nil, fmt.Errorf("pod: resolve %q: %w", addr, err)
	}
	conn, err := net.ListenUDP("udp", udpAddr)
	if err != nil {
		return nil, fmt.Errorf("pod: listen %q: %w", addr, err)
	}
	return &UDP{conn: conn, peerCh: make(chan net.Addr, 1)}, nil
}

func (u *UDP) Send(f wire.Frame) error {
	if u.lastPeer == nil {
		// No peer learned yet — drop the send rather than blast a default.
		return fmt.Errorf("pod/udp: no peer known yet")
	}
	buf := make([]byte, 1024)
	n, err := wire.Encode(f, buf)
	if err != nil {
		return fmt.Errorf("pod/udp: encode: %w", err)
	}
	_, err = u.conn.WriteTo(buf[:n], u.lastPeer)
	return err
}

func (u *UDP) Recv(ctx context.Context) (wire.Frame, string, error) {
	// Plumb cancellation by closing the conn on ctx.Done. Multiple Recv
	// callers would race here, but Client.Run only has one consumer.
	stopOnDone := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = u.conn.SetReadDeadline(timeInThePast())
		case <-stopOnDone:
		}
	}()
	defer close(stopOnDone)

	buf := make([]byte, 1500)
	for {
		n, peer, err := u.conn.ReadFromUDP(buf)
		if err != nil {
			if ctx.Err() != nil {
				return nil, "", ctx.Err()
			}
			return nil, "", err
		}
		frame, derr := wire.Decode(buf[:n])
		if derr != nil {
			// Corrupted or unknown frame — log via the caller's path is
			// awkward; just continue. Caller never sees garbage.
			continue
		}
		u.lastPeer = peer
		return frame, peer.String(), nil
	}
}

func (u *UDP) Close() error { return u.conn.Close() }
