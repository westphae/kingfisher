package store

import (
	"database/sql"
	"os"
	"path/filepath"
	"testing"
)

// touch creates a file with the given size (bytes of zero) under dir.
func touch(t *testing.T, path string, size int) {
	t.Helper()
	b := make([]byte, size)
	if err := os.WriteFile(path, b, 0o644); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// A cold DB with empty (0-byte) sidecars has them removed, main file untouched.
func TestSweepSidecarsRemovesEmpty(t *testing.T) {
	dir := t.TempDir()
	db := filepath.Join(dir, "flight.db")
	touch(t, db, 4096)
	touch(t, db+"-wal", 0)
	touch(t, db+"-shm", 32768)

	if err := SweepSidecars(dir, ""); err != nil {
		t.Fatalf("SweepSidecars: %v", err)
	}
	if !exists(db) {
		t.Errorf("main DB was removed")
	}
	if exists(db+"-wal") || exists(db+"-shm") {
		t.Errorf("sidecars not removed: wal=%v shm=%v", exists(db+"-wal"), exists(db+"-shm"))
	}
}

// exceptPath (the active DB) keeps its sidecars; every other DB is swept.
func TestSweepSidecarsSkipsActive(t *testing.T) {
	dir := t.TempDir()
	active := filepath.Join(dir, "active.db")
	cold := filepath.Join(dir, "cold.db")
	for _, base := range []string{active, cold} {
		touch(t, base, 4096)
		touch(t, base+"-wal", 0)
		touch(t, base+"-shm", 32768)
	}

	if err := SweepSidecars(dir, active); err != nil {
		t.Fatalf("SweepSidecars: %v", err)
	}
	if !exists(active+"-wal") || !exists(active+"-shm") {
		t.Errorf("active DB sidecars were swept")
	}
	if exists(cold+"-wal") || exists(cold+"-shm") {
		t.Errorf("cold DB sidecars not swept")
	}
}

// A non-empty -wal (unclean close) is finalized: the WAL is checkpointed into a
// real DB and the now-empty sidecars removed, with the data still readable.
func TestSweepSidecarsFinalizesDirtyWAL(t *testing.T) {
	dir := t.TempDir()
	st, err := Open(dir, "n456t")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	path := st.Path()
	if err := st.EnsureTable("gps", []string{"lat"}); err != nil {
		t.Fatalf("EnsureTable: %v", err)
	}
	if _, err := st.DB().Exec(`INSERT INTO gps(ts_ns, lat) VALUES (1, 42.5)`); err != nil {
		t.Fatalf("insert: %v", err)
	}
	// Close the *sql.DB directly (bypassing Store.Close) so no checkpoint/cleanup
	// runs — mimicking an ungraceful stop that leaves a non-empty WAL behind.
	if err := st.DB().Close(); err != nil {
		t.Fatalf("db close: %v", err)
	}
	st.db = nil
	if fi, err := os.Stat(path + "-wal"); err != nil || fi.Size() == 0 {
		t.Skipf("test precondition: expected a non-empty -wal, got err=%v", err)
	}

	if err := SweepSidecars(dir, ""); err != nil {
		t.Fatalf("SweepSidecars: %v", err)
	}
	if exists(path+"-wal") || exists(path+"-shm") {
		t.Errorf("sidecars not removed after finalize")
	}
	// The row survived the checkpoint into the main file. Open the finalized
	// path directly (Open would mint a new timestamped DB, not reopen this one).
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatalf("reopen finalized: %v", err)
	}
	defer db.Close()
	var lat float64
	if err := db.QueryRow(`SELECT lat FROM gps LIMIT 1`).Scan(&lat); err != nil {
		t.Fatalf("read finalized row: %v", err)
	}
	if lat != 42.5 {
		t.Errorf("lat = %v, want 42.5", lat)
	}
}
