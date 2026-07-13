package flights

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/store"
)

// buildDB writes a synthetic flight DB via the real store layer and returns
// its path. rows is a sequence of (secOffset, lat, lon, gsMS).
func buildDB(t *testing.T, dir string, rows [][4]float64) string {
	t.Helper()
	st, err := store.Open(dir, "test")
	if err != nil {
		t.Fatalf("store.Open: %v", err)
	}
	cols := []string{"lat", "lon", "gs", "fix", "alt_msl"}
	if err := st.EnsureTable("gps", cols); err != nil {
		t.Fatalf("EnsureTable: %v", err)
	}
	base := time.Date(2026, 7, 13, 15, 0, 0, 0, time.UTC).UnixNano()
	var samples []live.Sample
	for _, r := range rows {
		samples = append(samples, live.Sample{
			Device: "gps",
			TsNs:   base + int64(r[0]*1e9),
			Values: map[string]float64{
				"lat": r[1], "lon": r[2], "gs": r[3], "fix": 3,
				"alt_msl": 180 + r[3]*10, // altitude loosely tracks speed; peak in cruise
			},
		})
	}
	if err := st.FlushBatch("gps", cols, samples); err != nil {
		t.Fatalf("FlushBatch: %v", err)
	}
	path := st.Path()
	if err := st.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	return path
}

// KTKI 33.1779,-96.5905 → KADS 32.9686,-96.8364
func flightRows() [][4]float64 {
	var rows [][4]float64
	sec := 0.0
	add := func(n int, lat, lon, gs float64) {
		for i := 0; i < n; i++ {
			rows = append(rows, [4]float64{sec, lat, lon, gs})
			sec += 1
		}
	}
	add(60, 33.1779, -96.5905, 3)  // taxi at KTKI
	add(30, 33.1780, -96.5900, 35) // takeoff roll + climb
	add(300, 33.07, -96.71, 70)    // cruise toward KADS
	add(30, 32.9690, -96.8360, 30) // approach
	add(20, 32.9686, -96.8364, 2)  // rollout/taxi at KADS
	return rows
}

func TestScanDBFlight(t *testing.T) {
	dir := t.TempDir()
	path := buildDB(t, dir, flightRows())
	sum, err := ScanDB(path)
	if err != nil {
		t.Fatalf("ScanDB: %v", err)
	}
	if sum.Ground {
		t.Errorf("flight classified as ground session")
	}
	if sum.Legs != 1 {
		t.Errorf("legs = %d, want 1", sum.Legs)
	}
	if sum.Takeoff == nil || sum.Takeoff.Ident != "KTKI" {
		t.Errorf("takeoff = %+v, want KTKI", sum.Takeoff)
	}
	if sum.Landing == nil || sum.Landing.Ident != "KADS" {
		t.Errorf("landing = %+v, want KADS", sum.Landing)
	}
	if sum.AirborneS < 300 || sum.AirborneS > 420 {
		t.Errorf("airborne = %.0fs, want ~360", sum.AirborneS)
	}
	if sum.DurationS < 430 || sum.DurationS > 450 {
		t.Errorf("duration = %.0fs, want ~439", sum.DurationS)
	}
	if sum.MaxAltMslM < 800 {
		t.Errorf("max alt = %.0f m, want ~880", sum.MaxAltMslM)
	}
}

func TestScanDBGroundSession(t *testing.T) {
	dir := t.TempDir()
	var rows [][4]float64
	for s := 0; s < 120; s++ {
		rows = append(rows, [4]float64{float64(s), 33.1779, -96.5905, 1.5}) // parked
	}
	path := buildDB(t, dir, rows)
	sum, err := ScanDB(path)
	if err != nil {
		t.Fatalf("ScanDB: %v", err)
	}
	if !sum.Ground || sum.Legs != 0 {
		t.Errorf("ground=%v legs=%d, want ground session", sum.Ground, sum.Legs)
	}
	if sum.Takeoff != nil || sum.Landing != nil {
		t.Errorf("ground session has airports: %+v %+v", sum.Takeoff, sum.Landing)
	}
}

func TestManagerCacheAndNotes(t *testing.T) {
	dir := t.TempDir()
	path := buildDB(t, dir, flightRows())
	base := filepath.Base(path)

	m := NewManager(dir)
	m.cachePath = filepath.Join(dir, "cache.json") // keep the test hermetic

	sums, _ := m.List("")
	if len(sums) != 1 || sums[0].ScanError != "pending" {
		t.Fatalf("first List: %+v", sums)
	}
	// Wait for the async scan.
	deadline := time.Now().Add(10 * time.Second)
	for {
		sums, scanning := m.List("")
		if !scanning && len(sums) == 1 && sums[0].ScanError == "" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("scan did not complete: %+v", sums)
		}
		time.Sleep(50 * time.Millisecond)
	}

	if err := m.UpdateNotes(base, "first solo to Addison"); err != nil {
		t.Fatalf("UpdateNotes: %v", err)
	}
	sums, _ = m.List("")
	if sums[0].Notes != "first solo to Addison" {
		t.Errorf("notes = %q after update", sums[0].Notes)
	}
	// No sidecars left behind by the notes write or by a fresh scan.
	if _, err := ScanDB(path); err != nil {
		t.Fatalf("rescan after notes: %v", err)
	}
	for _, ext := range []string{"-wal", "-shm"} {
		if _, err := os.Stat(path + ext); err == nil {
			t.Errorf("sidecar %s left behind", ext)
		}
	}
	// Active DB is skipped.
	sums, _ = m.List(base)
	if len(sums) != 0 {
		t.Errorf("active DB not skipped: %+v", sums)
	}
}
