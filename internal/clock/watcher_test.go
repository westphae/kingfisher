package clock

import (
	"testing"
	"time"

	"github.com/westphae/kingfisher/internal/store"
)

func rowCount(t *testing.T, st *store.Store) (n int, notes []string) {
	t.Helper()
	rows, err := st.DB().Query(`SELECT note FROM clock_offsets ORDER BY monotonic_ns`)
	if err != nil {
		t.Fatalf("query clock_offsets: %v", err)
	}
	defer rows.Close()
	for rows.Next() {
		var note string
		if err := rows.Scan(&note); err != nil {
			t.Fatalf("scan: %v", err)
		}
		notes = append(notes, note)
		n++
	}
	return n, notes
}

func TestWatcherOffsetRows(t *testing.T) {
	st, err := store.Open(t.TempDir(), "test")
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	defer st.Close()

	w := NewWatcher(st)
	w.begin() // anchor row

	wall, mono := clockPair()

	// Sub-threshold drift without force: no row.
	w.observe(wall+int64(5*time.Millisecond), mono, "slew", false)
	if n, _ := rowCount(t, st); n != 1 {
		t.Fatalf("sub-threshold slew wrote a row; rows=%d", n)
	}

	// Above-threshold drift: slew row.
	w.observe(wall+int64(50*time.Millisecond), mono, "slew", false)

	// Forced step, even small: row.
	w.observe(wall+int64(52*time.Millisecond), mono, "step", true)

	n, notes := rowCount(t, st)
	if n != 3 {
		t.Fatalf("rows = %d, want 3 (anchor, slew, step): %v", n, notes)
	}
	want := []string{"anchor", "slew", "step"}
	for i, wnt := range want {
		if notes[i] != wnt {
			t.Errorf("row %d note = %q, want %q", i, notes[i], wnt)
		}
	}

	// Deltas: slew row ≈ +50 ms from anchor, step row ≈ +2 ms from slew row.
	var deltas []int64
	rows, err := st.DB().Query(`SELECT delta_ns FROM clock_offsets ORDER BY monotonic_ns`)
	if err != nil {
		t.Fatalf("query deltas: %v", err)
	}
	defer rows.Close()
	for rows.Next() {
		var d int64
		if err := rows.Scan(&d); err != nil {
			t.Fatalf("scan: %v", err)
		}
		deltas = append(deltas, d)
	}
	if deltas[0] != 0 {
		t.Errorf("anchor delta = %d, want 0", deltas[0])
	}
	tol := int64(10 * time.Millisecond) // clockPair skew between begin() and here
	if got, want := deltas[1], int64(50*time.Millisecond); absNs(got-want) > tol {
		t.Errorf("slew delta = %dms, want ≈50ms", got/1e6)
	}
	if got, want := deltas[2], int64(2*time.Millisecond); absNs(got-want) > tol {
		t.Errorf("step delta = %dms, want ≈2ms", got/1e6)
	}
}
