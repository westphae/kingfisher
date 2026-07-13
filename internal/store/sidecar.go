package store

import (
	"database/sql"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
)

// SweepSidecars finalizes leftover WAL/SHM sidecars for the cold flight DBs in
// dir, skipping exceptPath (the DB the running process currently holds open, if
// any). It is meant to run once at startup to clean up the sidecars that a
// graceful close would normally remove but that survive an ungraceful stop
// (power cut, SIGKILL) or a `systemctl stop` that outran the checkpoint.
//
// For each "<name>.db" other than exceptPath:
//   - a 0-byte or absent -wal means the DB is fully checkpointed → its -wal/-shm
//     are removed;
//   - a non-empty -wal means the DB was closed uncleanly → it is reopened,
//     checkpointed (TRUNCATE) to merge the frames into the main file, closed,
//     and its now-empty sidecars removed.
//
// Errors are collected and returned but are non-fatal to the caller — a single
// unreadable DB must never block recorder startup.
func SweepSidecars(dir, exceptPath string) error {
	dbs, err := filepath.Glob(filepath.Join(dir, "*.db"))
	if err != nil {
		return err
	}
	var swept int
	var errs []string
	for _, path := range dbs {
		if path == exceptPath {
			continue
		}
		wfi, werr := os.Stat(path + "-wal")
		_, serr := os.Stat(path + "-shm")
		if werr != nil && serr != nil {
			continue // no sidecars to sweep
		}
		swept++
		if werr == nil && wfi.Size() > 0 {
			// Unclean close: merge the WAL before discarding the sidecars.
			if err := finalizeSidecars(path); err != nil {
				errs = append(errs, fmt.Sprintf("%s: %v", filepath.Base(path), err))
				swept--
				continue
			}
		}
		removeEmptySidecars(path)
	}
	if swept > 0 {
		log.Printf("store: swept %d flight-DB sidecar set(s)", swept)
	}
	if len(errs) > 0 {
		return fmt.Errorf("store: sweep sidecars: %s", strings.Join(errs, "; "))
	}
	return nil
}

// finalizeSidecars opens an existing, closed flight DB, checkpoints its WAL into
// the main file (TRUNCATE) so the -wal drops to 0 bytes, then closes it. The
// caller removes the now-empty sidecars. Same pragma string as Open so the DB is
// touched under identical WAL settings.
func finalizeSidecars(path string) error {
	db, err := sql.Open("sqlite", path+"?_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)&_pragma=busy_timeout(5000)")
	if err != nil {
		return err
	}
	defer db.Close()
	if err := db.Ping(); err != nil {
		return err
	}
	_, err = db.Exec(`PRAGMA wal_checkpoint(TRUNCATE)`)
	return err
}
