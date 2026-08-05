package sensors

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// Cabin ICM-45686 IIO pair (shared chip / FIFO).
const (
	CabinIMUGyro  = "icm45686-gyro"
	CabinIMUAccel = "icm45686-accel"
)

// CabinIMUPair returns the accel+gyro device names that must quiesce together
// before writing on-chip calibbias (OFFUSER).
func CabinIMUPair() []string {
	return []string{CabinIMUGyro, CabinIMUAccel}
}

// BufferGate coordinates pausing buffered IIO capture so sysfs attrs that are
// EBUSY while the buffer is enabled (notably calibbias) can be written safely.
type BufferGate struct {
	mu   sync.Mutex
	ctls map[string]*bufferCtl
}

type bufferCtl struct {
	req chan *pauseOp
}

type pauseOp struct {
	paused chan struct{} // closed when the buffer is down
	resume chan struct{} // closed to allow the loop to reopen
	once   sync.Once
}

func (op *pauseOp) signalResume() {
	op.once.Do(func() { close(op.resume) })
}

func NewBufferGate() *BufferGate {
	return &BufferGate{ctls: make(map[string]*bufferCtl)}
}

func (g *BufferGate) register(name string) *bufferCtl {
	if g == nil {
		return nil
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	c := &bufferCtl{req: make(chan *pauseOp)}
	g.ctls[name] = c
	return c
}

func (g *BufferGate) unregister(name string) {
	if g == nil {
		return
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	delete(g.ctls, name)
}

func (g *BufferGate) get(name string) *bufferCtl {
	if g == nil {
		return nil
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.ctls[name]
}

// WithPaused closes IIO buffers for each registered name, runs fn, then
// resumes capture. Names with no active buffered loop are skipped (attrs can
// still be written via the Reader when nothing holds the buffer open).
func (g *BufferGate) WithPaused(ctx context.Context, names []string, fn func() error) error {
	if g == nil {
		return fn()
	}
	const pauseTimeout = 15 * time.Second
	ops := make([]*pauseOp, 0, len(names))
	release := func() {
		for _, op := range ops {
			op.signalResume()
		}
	}
	for _, n := range names {
		c := g.get(n)
		if c == nil {
			continue
		}
		op := &pauseOp{
			paused: make(chan struct{}),
			resume: make(chan struct{}),
		}
		select {
		case <-ctx.Done():
			release()
			return ctx.Err()
		case c.req <- op:
			ops = append(ops, op)
		case <-time.After(pauseTimeout):
			release()
			return fmt.Errorf("sensors: pause %s: buffer loop not responding", n)
		}
	}
	for _, op := range ops {
		select {
		case <-ctx.Done():
			release()
			return ctx.Err()
		case <-op.paused:
		case <-time.After(pauseTimeout):
			release()
			return fmt.Errorf("sensors: pause: timed out waiting for buffer down")
		}
	}
	err := fn()
	release()
	return err
}
