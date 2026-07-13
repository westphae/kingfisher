// Package store owns the per-flight SQLite database. One DB per process
// lifetime; one table per data source; columns are added lazily the first
// time a new channel appears in an arriving sample. Writes batch through
// Buffer so the disk sees a transaction every FlushSeconds rather than per
// sample.
package store

import (
	"database/sql"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"

	"github.com/westphae/kingfisher/internal/live"
)

type Store struct {
	db   *sql.DB
	path string

	mu        sync.Mutex
	fallback  bool      // named unsynced_NNNN_* because the clock was insane at Open
	trueStart time.Time // real session start, learned once the clock syncs (zero until then)
}

// saneClockYear is the earliest wall-clock year considered plausible at Open.
// Below it the RTC is assumed dead/unset (e.g. 1970) and the DB gets a
// clock-independent fallback name instead of a garbage timestamp.
const saneClockYear = 2025

// Open creates the flight DB at <dir>/<rfc3339>_<tail>.db and initialises the
// metadata + _session tables. The filename timestamp is taken from the host
// wall clock, so deployment should start kingfisher only after GNSS discipline
// is healthy if cold-boot naming accuracy matters.
func Open(dir, tail string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	safeTail := sanitize(tail)
	if safeTail == "" {
		safeTail = "unknown"
	}
	var path string
	fallback := time.Now().Year() < saneClockYear
	if fallback {
		// RTC dead/unset: a timestamp name would be garbage (and could collide
		// across boots). Use a scan-and-increment sequence instead; Close renames
		// to the corrected timestamp once the true start time is learned.
		path = nextUnsyncedPath(dir, safeTail)
	} else {
		stamp := time.Now().UTC().Format("20060102T150405Z")
		path = filepath.Join(dir, fmt.Sprintf("%s_%s.db", stamp, safeTail))
	}
	db, err := sql.Open("sqlite", path+"?_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)&_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, err
	}
	s := &Store{db: db, path: path, fallback: fallback}
	if err := s.bootstrap(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Path() string { return s.path }

// FallbackNamed reports whether the DB was opened with a clock-independent
// unsynced_NNNN name because the wall clock was implausible at Open.
func (s *Store) FallbackNamed() bool { return s.fallback }

// SetTrueStart records the real session start time, learned after the clock
// synced (true start = now − monotonic elapsed since open). Close uses it to
// rename a fallback-named DB to its corrected timestamp name.
func (s *Store) SetTrueStart(t time.Time) {
	s.mu.Lock()
	s.trueStart = t
	s.mu.Unlock()
}

// nextUnsyncedPath returns dir/unsynced_NNNN_<tail>.db with NNNN one greater
// than the highest existing sequence in dir — unique across boots without
// consulting the (untrusted) wall clock.
func nextUnsyncedPath(dir, tail string) string {
	max := 0
	if matches, err := filepath.Glob(filepath.Join(dir, "unsynced_*_*.db")); err == nil {
		re := regexp.MustCompile(`^unsynced_(\d+)_`)
		for _, m := range matches {
			if sm := re.FindStringSubmatch(filepath.Base(m)); sm != nil {
				if n, err := strconv.Atoi(sm[1]); err == nil && n > max {
					max = n
				}
			}
		}
	}
	return filepath.Join(dir, fmt.Sprintf("unsynced_%04d_%s.db", max+1, tail))
}

func (s *Store) DB() *sql.DB { return s.db }

// Size reports total on-disk bytes for this flight DB: the main file plus
// SQLite WAL/SHM sidecars when present.
func (s *Store) Size() int64 {
	var total int64
	for _, p := range []string{s.path, s.path + "-wal", s.path + "-shm"} {
		fi, err := os.Stat(p)
		if err != nil {
			continue
		}
		total += fi.Size()
	}
	return total
}

// VolumeFreeBytes reports space available to unprivileged users on the
// filesystem that contains the flight DB.
func (s *Store) VolumeFreeBytes() (int64, error) {
	return volumeFreeBytes(s.path)
}

// VolumeStats reports available and total bytes on the filesystem that
// contains the flight DB.
func (s *Store) VolumeStats() (free, total int64, err error) {
	return volumeStats(s.path)
}

func (s *Store) Close() error {
	if s.db == nil {
		return nil
	}
	if err := s.CheckpointWAL(); err != nil {
		log.Printf("store: close checkpoint: %v", err)
	}
	err := s.db.Close()
	s.db = nil
	if err == nil {
		// The modernc driver leaves the -wal/-shm sidecars on disk after close.
		// A successful TRUNCATE checkpoint just above leaves the -wal at 0 bytes,
		// so the sidecars carry no unmerged data and are safe to delete. If the
		// -wal is non-empty (checkpoint failed) it is kept; SweepSidecars will
		// finalize it on a later startup.
		removeEmptySidecars(s.path)
		s.renameFallback()
	}
	return err
}

// renameFallback renames a fallback-named DB to its corrected timestamp name
// once the handle is closed and the true start time is known. Renaming while
// open would be unsafe: the -wal/-shm sidecar paths are derived from the main
// path at open time. Crashed sessions keep the unsynced_ name for manual
// handling.
func (s *Store) renameFallback() {
	s.mu.Lock()
	trueStart := s.trueStart
	s.mu.Unlock()
	if !s.fallback || trueStart.IsZero() {
		return
	}
	base := filepath.Base(s.path)
	i := strings.Index(base, "_")
	j := strings.Index(base[i+1:], "_")
	if i < 0 || j < 0 {
		return
	}
	tail := base[i+1+j+1:] // "<tail>.db"
	stamp := trueStart.UTC().Format("20060102T150405Z")
	dest := filepath.Join(filepath.Dir(s.path), fmt.Sprintf("%s_%s", stamp, tail))
	if _, err := os.Stat(dest); err == nil {
		log.Printf("store: fallback rename target %s already exists; keeping %s", filepath.Base(dest), base)
		return
	}
	if err := os.Rename(s.path, dest); err != nil {
		log.Printf("store: fallback rename: %v", err)
		return
	}
	log.Printf("store: renamed %s -> %s (clock synced during session)", base, filepath.Base(dest))
	s.path = dest
}

// removeEmptySidecars deletes the -wal/-shm sidecars for a flight DB, but only
// when the -wal is absent or 0 bytes — i.e. fully checkpointed, so removal loses
// no data. A non-empty -wal is left untouched.
func removeEmptySidecars(path string) {
	wal := path + "-wal"
	if fi, err := os.Stat(wal); err == nil && fi.Size() > 0 {
		return
	}
	os.Remove(wal)
	os.Remove(path + "-shm")
}

// CheckpointWAL copies all WAL frames into the main DB file and truncates
// the WAL. Call after flushing pending rows so a pause checkpoint is durable.
func (s *Store) CheckpointWAL() error {
	if s.db == nil {
		return fmt.Errorf("store: closed")
	}
	_, err := s.db.Exec(`PRAGMA wal_checkpoint(TRUNCATE)`)
	return err
}

func (s *Store) bootstrap() error {
	_, err := s.db.Exec(`
CREATE TABLE IF NOT EXISTS metadata (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS _session (
  start_time   TEXT,
  aircraft     TEXT,
  aircraft_name TEXT,
  notes        TEXT,
  declination  REAL,
  version      TEXT
);
CREATE TABLE IF NOT EXISTS sensor_attrs (
  ts_ns     INTEGER NOT NULL,
  device    TEXT    NOT NULL,
  location  TEXT    NOT NULL DEFAULT 'hub',
  channel   TEXT,
  attr      TEXT    NOT NULL,
  value     TEXT
);
CREATE INDEX IF NOT EXISTS sensor_attrs_dev_attr ON sensor_attrs(device, channel, attr);
CREATE TABLE IF NOT EXISTS clock_offsets (
  ts_ns        INTEGER NOT NULL,
  monotonic_ns INTEGER NOT NULL,
  delta_ns     INTEGER NOT NULL,
  note         TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS howgozit_log (
  log_id        TEXT PRIMARY KEY,
  template_id   TEXT NOT NULL,
  display_name  TEXT NOT NULL,
  schema_json   TEXT NOT NULL,
  table_name    TEXT NOT NULL UNIQUE,
  created_ts_ns INTEGER NOT NULL
);`)
	if err != nil {
		return err
	}
	return s.migrateSensorAttrsLocation()
}

func (s *Store) migrateSensorAttrsLocation() error {
	rows, err := s.db.Query(`PRAGMA table_info(sensor_attrs)`)
	if err != nil {
		return err
	}
	defer rows.Close()
	hasLoc := false
	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, pk int
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			return err
		}
		if name == "location" {
			hasLoc = true
		}
	}
	if hasLoc {
		return rows.Err()
	}
	_, err = s.db.Exec(`ALTER TABLE sensor_attrs ADD COLUMN location TEXT NOT NULL DEFAULT 'hub'`)
	return err
}

// AttrRecord is one sysfs attribute reading. Channel is empty for
// device-level attrs (e.g. "sampling_frequency").
type AttrRecord struct {
	Channel string
	Attr    string
	Value   string
}

// LogAttrs writes a snapshot of sensor attributes for `device`. location is
// "hub" (cabin IIO) or "pod" (wing). Rows are timestamped with the current
// host wall clock, matching the same time base used for DB naming and session
// start. Pass only the attrs that have actually changed (or all of them at
// session start) — there is no dedup on the table.
func (s *Store) LogAttrs(device, location string, recs []AttrRecord) error {
	if len(recs) == 0 {
		return nil
	}
	if location == "" {
		location = "hub"
	}
	ts := time.Now().UnixNano()
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	stmt, err := tx.Prepare(`INSERT INTO sensor_attrs(ts_ns,device,location,channel,attr,value) VALUES(?,?,?,?,?,?)`)
	if err != nil {
		tx.Rollback()
		return err
	}
	for _, r := range recs {
		if _, err := stmt.Exec(ts, device, location, r.Channel, r.Attr, r.Value); err != nil {
			stmt.Close()
			tx.Rollback()
			return err
		}
	}
	stmt.Close()
	return tx.Commit()
}

// LogClockOffset appends one row to the clock_offsets table: a snapshot of the
// CLOCK_REALTIME↔CLOCK_MONOTONIC mapping. One anchor row is written at startup
// (delta 0), then a row whenever the offset shifts significantly (a chrony
// step, or accumulated slew). The rows form a piecewise mapping that lets
// post-flight analysis reconstruct a continuous timeline from wall-clock ts_ns
// stamps. See docs/timestamps.md.
func (s *Store) LogClockOffset(tsNs, monotonicNs, deltaNs int64, note string) error {
	_, err := s.db.Exec(`INSERT INTO clock_offsets(ts_ns,monotonic_ns,delta_ns,note) VALUES(?,?,?,?)`,
		tsNs, monotonicNs, deltaNs, note)
	return err
}

// SetMeta inserts/updates one row in the metadata key/value table.
func (s *Store) SetMeta(key, value string) error {
	_, err := s.db.Exec(`INSERT INTO metadata(key,value) VALUES(?,?)
  ON CONFLICT(key) DO UPDATE SET value=excluded.value`, key, value)
	return err
}

// WriteSession records the session header row. Caller should fill what's known
// at startup; declination can be updated later via SetMeta. start_time is on
// the host wall clock, after any external GNSS/chrony discipline already in
// effect.
func (s *Store) WriteSession(aircraft, aircraftName, notes, version string) error {
	_, err := s.db.Exec(`INSERT INTO _session(start_time,aircraft,aircraft_name,notes,declination,version)
  VALUES(?,?,?,?,?,?)`,
		time.Now().UTC().Format(time.RFC3339), aircraft, aircraftName, notes, 0.0, version)
	return err
}

// UpdateNotes rewrites the session notes (single _session row). Used by the
// Flights page for the currently-recording DB; closed DBs go through
// flights.Manager.UpdateNotes instead.
func (s *Store) UpdateNotes(notes string) error {
	_, err := s.db.Exec(`UPDATE _session SET notes=?`, notes)
	return err
}

// EnsureTable creates the per-device table with ts_ns + the named columns if
// it does not yet exist, or ALTERs it to add columns that arrived later.
// Columns are REAL; ts_ns is INTEGER NOT NULL. Table and column names are
// sanitized.
func (s *Store) EnsureTable(device string, columns []string) error {
	tbl := sanitize(device)
	if tbl == "" {
		return fmt.Errorf("store: empty device name")
	}
	cols := make([]string, 0, len(columns))
	for _, c := range columns {
		sc := sanitize(c)
		if sc == "" || sc == "ts_ns" {
			continue
		}
		cols = append(cols, sc)
	}
	if len(cols) == 0 {
		// Create an empty table with just ts_ns; columns will be added later.
		_, err := s.db.Exec(fmt.Sprintf(`CREATE TABLE IF NOT EXISTS %q (ts_ns INTEGER NOT NULL)`, tbl))
		return err
	}
	colDecl := make([]string, 0, len(cols))
	for _, c := range cols {
		colDecl = append(colDecl, fmt.Sprintf(`%q REAL`, c))
	}
	stmt := fmt.Sprintf(`CREATE TABLE IF NOT EXISTS %q (ts_ns INTEGER NOT NULL, %s)`,
		tbl, strings.Join(colDecl, ", "))
	if _, err := s.db.Exec(stmt); err != nil {
		return err
	}
	return s.addMissingColumns(tbl, cols)
}

func (s *Store) addMissingColumns(tbl string, want []string) error {
	rows, err := s.db.Query(fmt.Sprintf(`PRAGMA table_info(%q)`, tbl))
	if err != nil {
		return err
	}
	defer rows.Close()
	have := map[string]bool{}
	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, pk int
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			return err
		}
		have[name] = true
	}
	for _, c := range want {
		if have[c] {
			continue
		}
		if _, err := s.db.Exec(fmt.Sprintf(`ALTER TABLE %q ADD COLUMN %q REAL`, tbl, c)); err != nil {
			return err
		}
	}
	return nil
}

// FlushBatch writes all pending samples for one device in a single
// transaction. `samples` is in arrival order; missing channels write NULL.
func (s *Store) FlushBatch(device string, columns []string, samples []live.Sample) error {
	if len(samples) == 0 {
		return nil
	}
	tbl := sanitize(device)
	cols := make([]string, 0, len(columns))
	for _, c := range columns {
		sc := sanitize(c)
		if sc == "" || sc == "ts_ns" {
			continue
		}
		cols = append(cols, sc)
	}
	colList := []string{`"ts_ns"`}
	for _, c := range cols {
		colList = append(colList, fmt.Sprintf("%q", c))
	}
	placeholders := "(" + strings.Repeat("?,", len(colList)-1) + "?)"
	stmt := fmt.Sprintf(`INSERT INTO %q (%s) VALUES %s`,
		tbl, strings.Join(colList, ","),
		strings.Repeat(placeholders+",", len(samples)-1)+placeholders)

	args := make([]any, 0, len(samples)*len(colList))
	for _, sm := range samples {
		args = append(args, sm.TsNs)
		for _, c := range cols {
			if v, ok := sm.Values[c]; ok {
				args = append(args, v)
			} else {
				args = append(args, nil)
			}
		}
	}

	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	if _, err := tx.Exec(stmt, args...); err != nil {
		tx.Rollback()
		return err
	}
	return tx.Commit()
}

var sanitizeRe = regexp.MustCompile(`[^A-Za-z0-9_]+`)

// sanitize lower-cases, replaces non-identifier runs with underscores, and
// strips leading digits / leading underscores so the result is a safe SQL
// identifier and stable across rediscovery.
func sanitize(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	s = sanitizeRe.ReplaceAllString(s, "_")
	s = strings.Trim(s, "_")
	if s == "" {
		return ""
	}
	if s[0] >= '0' && s[0] <= '9' {
		s = "_" + s
	}
	return s
}

// Sanitize is exported for use by other packages that need to align column
// names with the table schema (e.g. the sensors reader applying a user-
// supplied column override).
func Sanitize(s string) string { return sanitize(s) }
