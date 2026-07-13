// Package flights builds per-flight summaries for the cockpit "Flights" page:
// date, duration, takeoff/landing airports, max altitude, notes, and backup
// state. Closed flight DBs are opened read-only, scanned once, and cached by
// (size, mtime) in a JSON file so the 400 MB backlog is only ever paid once.
// The active (recording) DB is never opened here.
package flights

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	_ "modernc.org/sqlite"

	"github.com/westphae/kingfisher/internal/airports"
)

// Airborne-detection thresholds. A Bonanza rotates well above 55 kt and
// taxis well below 20 kt; sustained windows reject GPS speed spikes.
const (
	takeoffSpeedMS  = 20.6 // 40 kt: sustained above → airborne
	landingSpeedMS  = 10.3 // 20 kt: sustained below → on the ground (hysteresis)
	sustainSec      = 10.0
	airportRadiusKm = 5.0
)

// AirportRef is a resolved takeoff/landing airport.
type AirportRef struct {
	Ident  string  `json:"ident"`
	Name   string  `json:"name"`
	DistKm float64 `json:"dist_km"`
}

// Summary is one flight DB's vital stats, as served to the UI.
type Summary struct {
	File      string      `json:"file"`
	SizeBytes int64       `json:"size_bytes"`
	StartUTC  string      `json:"start_utc,omitempty"`
	EndUTC    string      `json:"end_utc,omitempty"`
	DurationS float64     `json:"duration_s"`
	AirborneS float64     `json:"airborne_s"`
	Legs      int         `json:"legs"`
	Ground    bool        `json:"ground"` // never airborne → dev/ground session (route shows XXXX)
	Takeoff   *AirportRef `json:"takeoff,omitempty"`
	Landing   *AirportRef `json:"landing,omitempty"`
	// True when the gps stream begins already airborne (fix acquired after
	// takeoff) / ends still airborne (recording stopped before landing), so the
	// missing airport is a data boundary, not an unknown field.
	StartsAirborne bool    `json:"starts_airborne,omitempty"`
	EndsAirborne   bool    `json:"ends_airborne,omitempty"`
	MaxAltMslM     float64 `json:"max_alt_msl_m"`
	MaxPressAltFt  float64 `json:"max_press_alt_ft"`
	Notes          string  `json:"notes"`
	Aircraft       string  `json:"aircraft,omitempty"`
	Unsynced       bool    `json:"unsynced"` // fallback-named DB (clock was insane at open)
	ScanError      string  `json:"scan_error,omitempty"`

	// Set per-request by the web layer, not by the scanner (cached values are
	// overwritten on every response).
	Backup    string `json:"backup,omitempty"` // yes | stale | no | recording
	Recording bool   `json:"recording,omitempty"`
}

type cacheEntry struct {
	Size  int64   `json:"size"`
	Mtime int64   `json:"mtime_unix"`
	Sum   Summary `json:"summary"`
}

// Manager owns the summary cache and serializes background scans.
type Manager struct {
	dir       string
	cachePath string

	mu    sync.Mutex
	cache map[string]cacheEntry

	scanning atomic.Bool
}

func NewManager(dir string) *Manager {
	cachePath := filepath.Join(cacheDir(), "flight-summaries.json")
	m := &Manager{dir: dir, cachePath: cachePath, cache: map[string]cacheEntry{}}
	if b, err := os.ReadFile(cachePath); err == nil {
		if err := json.Unmarshal(b, &m.cache); err != nil {
			log.Printf("flights: cache unreadable (%v); rebuilding", err)
			m.cache = map[string]cacheEntry{}
		}
	}
	return m
}

func cacheDir() string {
	if d, err := os.UserCacheDir(); err == nil {
		p := filepath.Join(d, "kingfisher")
		if os.MkdirAll(p, 0o755) == nil {
			return p
		}
	}
	return os.TempDir()
}

func (m *Manager) saveCacheLocked() {
	b, err := json.Marshal(m.cache)
	if err != nil {
		return
	}
	tmp := m.cachePath + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err == nil {
		_ = os.Rename(tmp, m.cachePath)
	}
}

// List returns summaries for every closed *.db in the directory, newest first.
// Files without a fresh cache entry are returned as placeholders (ScanError
// "pending") and a background refresh is kicked; scanning reports whether one
// is still running.
func (m *Manager) List(activeBase string) (sums []Summary, scanning bool) {
	files, _ := filepath.Glob(filepath.Join(m.dir, "*.db"))
	stale := false
	m.mu.Lock()
	for _, f := range files {
		base := filepath.Base(f)
		if base == activeBase {
			continue
		}
		fi, err := os.Stat(f)
		if err != nil {
			continue
		}
		if e, ok := m.cache[base]; ok && e.Size == fi.Size() && e.Mtime == fi.ModTime().Unix() {
			sums = append(sums, e.Sum)
			continue
		}
		stale = true
		sums = append(sums, Summary{
			File: base, SizeBytes: fi.Size(),
			Unsynced:  strings.HasPrefix(base, "unsynced_"),
			ScanError: "pending",
		})
	}
	m.mu.Unlock()
	if stale {
		m.refreshAsync(activeBase)
	}
	sort.Slice(sums, func(i, j int) bool {
		si, sj := sums[i], sums[j]
		if si.StartUTC != sj.StartUTC {
			return si.StartUTC > sj.StartUTC
		}
		return si.File > sj.File
	})
	return sums, m.scanning.Load()
}

func (m *Manager) refreshAsync(activeBase string) {
	if !m.scanning.CompareAndSwap(false, true) {
		return
	}
	go func() {
		defer m.scanning.Store(false)
		files, _ := filepath.Glob(filepath.Join(m.dir, "*.db"))
		n := 0
		for _, f := range files {
			base := filepath.Base(f)
			if base == activeBase {
				continue
			}
			fi, err := os.Stat(f)
			if err != nil {
				continue
			}
			m.mu.Lock()
			e, ok := m.cache[base]
			fresh := ok && e.Size == fi.Size() && e.Mtime == fi.ModTime().Unix()
			m.mu.Unlock()
			if fresh {
				continue
			}
			sum, err := ScanDB(f)
			if err != nil {
				sum = Summary{File: base, SizeBytes: fi.Size(), ScanError: err.Error()}
			}
			sum.Unsynced = strings.HasPrefix(base, "unsynced_")
			m.mu.Lock()
			m.cache[base] = cacheEntry{Size: fi.Size(), Mtime: fi.ModTime().Unix(), Sum: sum}
			m.saveCacheLocked()
			m.mu.Unlock()
			n++
		}
		if n > 0 {
			log.Printf("flights: scanned %d flight DB(s)", n)
		}
	}()
}

// UpdateNotes writes notes into a closed flight DB's _session row, checkpoints
// so no WAL sidecar lingers, and patches the cache in place (no rescan).
func (m *Manager) UpdateNotes(base, notes string) error {
	path := filepath.Join(m.dir, base)
	db, err := sql.Open("sqlite", path+"?_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)&_pragma=busy_timeout(5000)")
	if err != nil {
		return err
	}
	defer db.Close()
	if _, err := db.Exec(`UPDATE _session SET notes=?`, notes); err != nil {
		return err
	}
	if _, err := db.Exec(`PRAGMA wal_checkpoint(TRUNCATE)`); err != nil {
		return err
	}
	if err := db.Close(); err != nil {
		return err
	}
	cleanEmptySidecars(path)
	fi, err := os.Stat(path)
	if err != nil {
		return err
	}
	m.mu.Lock()
	if e, ok := m.cache[base]; ok {
		e.Sum.Notes = notes
		e.Size = fi.Size()
		e.Mtime = fi.ModTime().Unix()
		m.cache[base] = e
		m.saveCacheLocked()
	}
	m.mu.Unlock()
	return nil
}

// ScanDB computes the Summary for one closed flight DB (read-only open).
func ScanDB(path string) (Summary, error) {
	fi, err := os.Stat(path)
	if err != nil {
		return Summary{}, err
	}
	sum := Summary{File: filepath.Base(path), SizeBytes: fi.Size()}

	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro&_pragma=busy_timeout(5000)")
	if err != nil {
		return sum, err
	}
	// Even a mode=ro open of a WAL DB materializes -wal/-shm (readers need the
	// shm index); drop them again after close so scans never re-litter the
	// directory the sidecar sweep just cleaned.
	defer func() {
		db.Close()
		cleanEmptySidecars(path)
	}()

	// Session header (all optional — partial DBs still summarize).
	var start, aircraft, notes sql.NullString
	_ = db.QueryRow(`SELECT start_time, aircraft, notes FROM _session LIMIT 1`).Scan(&start, &aircraft, &notes)
	sum.Aircraft = aircraft.String
	sum.Notes = notes.String
	sum.StartUTC = start.String
	// A fallback-named DB may carry the corrected start in metadata.
	var trueStart sql.NullString
	_ = db.QueryRow(`SELECT value FROM metadata WHERE key='clock_true_start_utc'`).Scan(&trueStart)
	if trueStart.Valid && trueStart.String != "" {
		if t, err := time.Parse(time.RFC3339Nano, trueStart.String); err == nil {
			if st, err2 := time.Parse(time.RFC3339, sum.StartUTC); err2 != nil || st.Year() < 2025 {
				sum.StartUTC = t.UTC().Format(time.RFC3339)
			}
		}
	}

	// Block time: min/max ts_ns across whichever streams exist.
	var minTs, maxTs int64
	for _, tbl := range []string{"gps", "system", "press_alt", "ahrs"} {
		var lo, hi sql.NullInt64
		if err := db.QueryRow(fmt.Sprintf(`SELECT MIN(ts_ns), MAX(ts_ns) FROM %q`, tbl)).Scan(&lo, &hi); err != nil {
			continue
		}
		if lo.Valid && (minTs == 0 || lo.Int64 < minTs) {
			minTs = lo.Int64
		}
		if hi.Valid && hi.Int64 > maxTs {
			maxTs = hi.Int64
		}
	}
	if minTs > 0 && maxTs > minTs {
		sum.DurationS = float64(maxTs-minTs) / 1e9
		sum.EndUTC = time.Unix(0, maxTs).UTC().Format(time.RFC3339)
		if sum.StartUTC == "" {
			sum.StartUTC = time.Unix(0, minTs).UTC().Format(time.RFC3339)
		}
	}

	// Max altitudes.
	var maxAlt sql.NullFloat64
	_ = db.QueryRow(`SELECT MAX(alt_msl) FROM gps WHERE fix >= 2`).Scan(&maxAlt)
	sum.MaxAltMslM = maxAlt.Float64
	var maxPa sql.NullFloat64
	_ = db.QueryRow(`SELECT MAX(pressure_alt_ft) FROM press_alt`).Scan(&maxPa)
	sum.MaxPressAltFt = maxPa.Float64

	// Airborne legs from the gps stream.
	legs, bounds, err := detectLegs(db)
	if err == nil {
		sum.Legs = len(legs)
		for _, l := range legs {
			sum.AirborneS += float64(l.endTs-l.startTs) / 1e9
		}
		if len(legs) > 0 {
			first, last := legs[0], legs[len(legs)-1]
			// A leg that starts at the stream's first sample means the fix was
			// acquired already airborne; one that runs to the last sample means
			// recording stopped before landing. Either way the endpoint is a
			// data boundary — don't snap it to whatever airport is below.
			sum.StartsAirborne = first.startTs <= bounds.firstTs
			sum.EndsAirborne = bounds.endedAirborne
			if !sum.StartsAirborne {
				if ap, d, ok := airports.Nearest(first.startLat, first.startLon, airportRadiusKm); ok {
					sum.Takeoff = &AirportRef{Ident: ap.Ident, Name: ap.Name, DistKm: d}
				}
			}
			if !sum.EndsAirborne {
				if ap, d, ok := airports.Nearest(last.endLat, last.endLon, airportRadiusKm); ok {
					sum.Landing = &AirportRef{Ident: ap.Ident, Name: ap.Name, DistKm: d}
				}
			}
		} else {
			sum.Ground = true
		}
	} else {
		sum.Ground = true // no usable gps stream — a home/bench session
	}
	return sum, nil
}

// cleanEmptySidecars removes a DB's -wal/-shm when the -wal is absent or fully
// checkpointed (0 bytes) — the same rule store.Close applies.
func cleanEmptySidecars(path string) {
	if fi, err := os.Stat(path + "-wal"); err == nil && fi.Size() > 0 {
		return
	}
	os.Remove(path + "-wal")
	os.Remove(path + "-shm")
}

type leg struct {
	startTs, endTs     int64
	startLat, startLon float64
	endLat, endLon     float64
}

// streamBounds describes the edges of the usable gps stream.
type streamBounds struct {
	firstTs, lastTs int64
	endedAirborne   bool
}

// detectLegs streams gps rows and applies a sustained-speed hysteresis:
// airborne when gs ≥ takeoffSpeedMS for sustainSec, ground again when
// gs ≤ landingSpeedMS for sustainSec. Returns one leg per airborne segment.
func detectLegs(db *sql.DB) ([]leg, streamBounds, error) {
	var b streamBounds
	rows, err := db.Query(`SELECT ts_ns, lat, lon, gs FROM gps
		WHERE fix >= 2 AND lat IS NOT NULL AND lon IS NOT NULL AND gs IS NOT NULL
		ORDER BY ts_ns`)
	if err != nil {
		return nil, b, err
	}
	defer rows.Close()

	var legs []leg
	var cur leg
	airborne := false
	// candidate transition start (0 = no candidate)
	var candTs int64
	var candLat, candLon float64
	any := false

	for rows.Next() {
		var ts int64
		var lat, lon, gs float64
		if err := rows.Scan(&ts, &lat, &lon, &gs); err != nil {
			return nil, b, err
		}
		if !any {
			b.firstTs = ts
		}
		b.lastTs = ts
		any = true
		if !airborne {
			if gs >= takeoffSpeedMS {
				if candTs == 0 {
					candTs, candLat, candLon = ts, lat, lon
				} else if float64(ts-candTs)/1e9 >= sustainSec {
					airborne = true
					cur = leg{startTs: candTs, startLat: candLat, startLon: candLon}
					candTs = 0
				}
			} else {
				candTs = 0
			}
		} else {
			cur.endTs, cur.endLat, cur.endLon = ts, lat, lon
			if gs <= landingSpeedMS {
				if candTs == 0 {
					candTs, candLat, candLon = ts, lat, lon
				} else if float64(ts-candTs)/1e9 >= sustainSec {
					airborne = false
					cur.endTs, cur.endLat, cur.endLon = candTs, candLat, candLon
					legs = append(legs, cur)
					candTs = 0
				}
			} else {
				candTs = 0
			}
		}
	}
	if err := rows.Err(); err != nil {
		return nil, b, err
	}
	if airborne && cur.endTs > cur.startTs {
		legs = append(legs, cur) // data ended mid-air (crash of recorder, not aircraft, one hopes)
		b.endedAirborne = true
	}
	if !any {
		return nil, b, nil
	}
	return legs, b, nil
}
