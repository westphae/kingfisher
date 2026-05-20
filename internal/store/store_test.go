package store

import (
	"path/filepath"
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/live"
)

func openTemp(t *testing.T) *Store {
	t.Helper()
	dir := t.TempDir()
	s, err := Open(filepath.Join(dir, "f"), "N12345")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	return s
}

func TestStoreBufferFlushWritesRows(t *testing.T) {
	s := openTemp(t)
	defer s.Close()
	buf := NewBuffer(s, 100*time.Millisecond)

	for i := 0; i < 5; i++ {
		buf.Append(live.Sample{
			Device: "icm20948",
			TsNs:   int64(i + 1),
			Values: map[string]float64{"accel_x": float64(i), "accel_y": 0.5, "accel_z": -1},
		})
		buf.Append(live.Sample{
			Device: "bmp280",
			TsNs:   int64(i + 1),
			Values: map[string]float64{"pressure": 1013.25, "temp": 22.5},
		})
	}
	if err := buf.Flush(); err != nil {
		t.Fatalf("flush: %v", err)
	}

	var n int
	if err := s.DB().QueryRow(`SELECT COUNT(*) FROM icm20948`).Scan(&n); err != nil {
		t.Fatalf("query icm20948: %v", err)
	}
	if n != 5 {
		t.Fatalf("icm20948 rows: got %d, want 5", n)
	}
	if err := s.DB().QueryRow(`SELECT COUNT(*) FROM bmp280`).Scan(&n); err != nil {
		t.Fatalf("query bmp280: %v", err)
	}
	if n != 5 {
		t.Fatalf("bmp280 rows: got %d, want 5", n)
	}

	// Spot-check a value round-trips correctly.
	var ax float64
	if err := s.DB().QueryRow(`SELECT accel_x FROM icm20948 ORDER BY ts_ns DESC LIMIT 1`).Scan(&ax); err != nil {
		t.Fatalf("query ax: %v", err)
	}
	if ax != 4 {
		t.Fatalf("last accel_x: got %v, want 4", ax)
	}
}

func TestStoreEnsureTableAddsMissingColumns(t *testing.T) {
	s := openTemp(t)
	defer s.Close()

	if err := s.EnsureTable("dev", []string{"a", "b"}); err != nil {
		t.Fatalf("ensure 1: %v", err)
	}
	if err := s.EnsureTable("dev", []string{"a", "b", "c"}); err != nil {
		t.Fatalf("ensure 2: %v", err)
	}
	rows, err := s.DB().Query(`PRAGMA table_info("dev")`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	cols := map[string]bool{}
	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, pk int
		var dflt any
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			t.Fatal(err)
		}
		cols[name] = true
	}
	for _, want := range []string{"ts_ns", "a", "b", "c"} {
		if !cols[want] {
			t.Fatalf("missing column %q in dev", want)
		}
	}
}

func TestSanitize(t *testing.T) {
	cases := map[string]string{
		"accel_x":    "accel_x",
		"Accel X":    "accel_x",
		"123abc":     "_123abc",
		"  pressure": "pressure",
		"":           "",
	}
	for in, want := range cases {
		if got := Sanitize(in); got != want {
			t.Errorf("Sanitize(%q)=%q want %q", in, got, want)
		}
	}
}
