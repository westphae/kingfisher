package ups

import (
	"errors"
	"testing"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
)

type fakeGauge struct {
	v, soc float64
	err    error
}

func (g *fakeGauge) ReadVoltageSOC() (float64, float64, error) { return g.v, g.soc, g.err }
func (g *fakeGauge) Version() (uint16, error)                  { return 0x0002, nil }
func (g *fakeGauge) Close() error                              { return nil }

type fakePLD struct {
	ac  bool
	err error
}

func (p *fakePLD) ACPresent() (bool, error) { return p.ac, p.err }
func (p *fakePLD) Close() error             { return nil }

func testMonitor(t *testing.T, og func() (Gauge, error), op func() (PLD, error)) (*Monitor, *live.Hub) {
	t.Helper()
	holder := config.NewHolder("", &config.Config{UPS: config.UPS{Enabled: true}})
	hub := live.NewHub()
	m := New(holder, hub, nil, nil, nil)
	m.openGauge = og
	m.openPLD = op
	return m, hub
}

func TestPollPublishesSampleKeys(t *testing.T) {
	m, hub := testMonitor(t,
		func() (Gauge, error) { return &fakeGauge{v: 4.05, soc: 87.5}, nil },
		func() (PLD, error) { return &fakePLD{ac: true}, nil },
	)
	m.poll()

	sm, ok := hub.SnapshotNow().Devices[Device]
	if !ok {
		t.Fatalf("no %q sample published", Device)
	}
	for _, k := range []string{"voltage_v", "soc_pct", "ac_ok", "on_battery_s", "gauge_ok", "pld_ok"} {
		if _, ok := sm.Values[k]; !ok {
			t.Errorf("missing channel %q in %v", k, sm.Values)
		}
	}
	if sm.Values["voltage_v"] != 4.05 || sm.Values["soc_pct"] != 87.5 || sm.Values["ac_ok"] != 1 {
		t.Errorf("unexpected values: %v", sm.Values)
	}
	if _, ok := sm.Values["time_remaining_s"]; ok {
		t.Errorf("time_remaining_s published before estimable")
	}

	snap := m.Status()
	if !snap.Present || !snap.PLDOk || !snap.ACOk || snap.SocPct != 87.5 {
		t.Errorf("snapshot: %+v", snap)
	}
}

// Open failures are an expected state (HAT absent, i2c-dev unloaded): no
// panic, no dead-source channels, Present=false.
func TestPollDegradesWithoutHardware(t *testing.T) {
	m, hub := testMonitor(t,
		func() (Gauge, error) { return nil, errors.New("no such device") },
		func() (PLD, error) { return nil, errors.New("no such chip") },
	)
	m.poll()

	sm, ok := hub.SnapshotNow().Devices[Device]
	if !ok {
		t.Fatalf("no %q sample published", Device)
	}
	if sm.Values["gauge_ok"] != 0 || sm.Values["pld_ok"] != 0 {
		t.Errorf("ok flags should be 0: %v", sm.Values)
	}
	for _, k := range []string{"voltage_v", "soc_pct", "ac_ok", "on_battery_s"} {
		if _, present := sm.Values[k]; present {
			t.Errorf("dead-source channel %q published: %v", k, sm.Values)
		}
	}
	snap := m.Status()
	if snap.Present || snap.PLDOk {
		t.Errorf("snapshot claims presence without hardware: %+v", snap)
	}
	if snap.LastError == "" {
		t.Errorf("snapshot should carry the open error")
	}
}

// A gauge read error drops the handle for reopen; the shutdown hook must
// not fire from degraded data.
func TestReadErrorDropsGaugeNoShutdown(t *testing.T) {
	fired := false
	g := &fakeGauge{v: 1.0, soc: 0, err: errors.New("i2c timeout")}
	m, _ := testMonitor(t,
		func() (Gauge, error) { return g, nil },
		func() (PLD, error) { return &fakePLD{ac: false}, nil },
	)
	m.shutdown = func(bool) { fired = true }

	for i := 0; i < 10; i++ {
		m.poll()
	}
	if fired {
		t.Fatalf("shutdown fired on gauge read errors")
	}
	if m.Status().Present {
		t.Errorf("gauge still marked present after read error")
	}
}
