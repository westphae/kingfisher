// Package store owns the per-flight SQLite database. One DB per process
// lifetime; one table per data source; columns are added lazily the first
// time a new channel appears in an arriving sample. Writes batch through
// Buffer so the disk sees a transaction every FlushSeconds rather than per
// sample.
package store

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	_ "modernc.org/sqlite"

	"github.com/westphae/kingfisher/internal/live"
)

type Store struct {
	db   *sql.DB
	path string
}

// Open creates the flight DB at <dir>/<rfc3339>_<tail>.db and initialises
// the metadata + _session tables.
func Open(dir, tail string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	stamp := time.Now().UTC().Format("20060102T150405Z")
	safeTail := sanitize(tail)
	if safeTail == "" {
		safeTail = "unknown"
	}
	path := filepath.Join(dir, fmt.Sprintf("%s_%s.db", stamp, safeTail))
	db, err := sql.Open("sqlite", path+"?_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)")
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, err
	}
	s := &Store{db: db, path: path}
	if err := s.bootstrap(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Path() string { return s.path }

func (s *Store) DB() *sql.DB { return s.db }

// Size reports the on-disk byte count, or 0 if the file is gone.
func (s *Store) Size() int64 {
	fi, err := os.Stat(s.path)
	if err != nil {
		return 0
	}
	return fi.Size()
}

func (s *Store) Close() error {
	if s.db == nil {
		return nil
	}
	err := s.db.Close()
	s.db = nil
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
  ts_ns   INTEGER NOT NULL,
  device  TEXT    NOT NULL,
  channel TEXT,
  attr    TEXT    NOT NULL,
  value   TEXT
);
CREATE INDEX IF NOT EXISTS sensor_attrs_dev_attr ON sensor_attrs(device, channel, attr);`)
	return err
}

// AttrRecord is one sysfs attribute reading. Channel is empty for
// device-level attrs (e.g. "sampling_frequency").
type AttrRecord struct {
	Channel string
	Attr    string
	Value   string
}

// LogAttrs writes a snapshot of sensor attributes for `device`. Rows are
// timestamped with the current wall clock. Pass only the attrs that have
// actually changed (or all of them at session start) — there is no dedup
// on the table.
func (s *Store) LogAttrs(device string, recs []AttrRecord) error {
	if len(recs) == 0 {
		return nil
	}
	ts := time.Now().UnixNano()
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	stmt, err := tx.Prepare(`INSERT INTO sensor_attrs(ts_ns,device,channel,attr,value) VALUES(?,?,?,?,?)`)
	if err != nil {
		tx.Rollback()
		return err
	}
	for _, r := range recs {
		if _, err := stmt.Exec(ts, device, r.Channel, r.Attr, r.Value); err != nil {
			stmt.Close()
			tx.Rollback()
			return err
		}
	}
	stmt.Close()
	return tx.Commit()
}

// SetMeta inserts/updates one row in the metadata key/value table.
func (s *Store) SetMeta(key, value string) error {
	_, err := s.db.Exec(`INSERT INTO metadata(key,value) VALUES(?,?)
  ON CONFLICT(key) DO UPDATE SET value=excluded.value`, key, value)
	return err
}

// WriteSession records the session header row. Caller should fill what's
// known at startup; declination can be updated later via SetMeta.
func (s *Store) WriteSession(aircraft, aircraftName, notes, version string) error {
	_, err := s.db.Exec(`INSERT INTO _session(start_time,aircraft,aircraft_name,notes,declination,version)
  VALUES(?,?,?,?,?,?)`,
		time.Now().UTC().Format(time.RFC3339), aircraft, aircraftName, notes, 0.0, version)
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
