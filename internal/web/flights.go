package web

// Flights summary API:
//
//	GET  /api/flights       — per-flight vital stats for every DB in db_dir
//	POST /api/flights/notes — update one flight's _session.notes
//
// Summaries come from the internal/flights cache (background scans); backup
// state is compared against the manifest the backup script writes after each
// successful NAS sync, so it works offline in the aircraft.

import (
	"bufio"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/westphae/kingfisher/internal/flights"
)

const maxNotesLen = 4000

func manifestPath() string {
	if d := os.Getenv("XDG_STATE_HOME"); d != "" {
		return filepath.Join(d, "kingfisher", "backup-manifest.tsv")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".local", "state", "kingfisher", "backup-manifest.tsv")
}

// readManifest parses "<name>\t<bytes>" lines; ageS is seconds since the last
// successful backup listing, -1 when no manifest exists.
func readManifest() (sizes map[string]int64, ageS int64) {
	sizes = map[string]int64{}
	p := manifestPath()
	if p == "" {
		return sizes, -1
	}
	fi, err := os.Stat(p)
	if err != nil {
		return sizes, -1
	}
	f, err := os.Open(p)
	if err != nil {
		return sizes, -1
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		name, sz, ok := strings.Cut(sc.Text(), "\t")
		if !ok {
			continue
		}
		if n, err := strconv.ParseInt(sz, 10, 64); err == nil {
			sizes[name] = n
		}
	}
	return sizes, int64(time.Since(fi.ModTime()).Seconds())
}

func backupState(sum *flights.Summary, manifest map[string]int64) string {
	n, ok := manifest[sum.File]
	switch {
	case !ok:
		return "no"
	case n == sum.SizeBytes:
		return "yes"
	default:
		return "stale"
	}
}

func (s *Server) handleFlights(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "GET only", http.StatusMethodNotAllowed)
		return
	}
	activeBase := filepath.Base(s.store.Path())
	sums, scanning := s.flightsMgr.List(activeBase)
	manifest, manifestAge := readManifest()
	for i := range sums {
		sums[i].Backup = backupState(&sums[i], manifest)
	}

	// Synthesize the active (recording) DB's row from the live store.
	var start, notes string
	_ = s.store.DB().QueryRow(`SELECT start_time, notes FROM _session LIMIT 1`).Scan(&start, &notes)
	active := flights.Summary{
		File:      activeBase,
		SizeBytes: s.store.Size(),
		StartUTC:  start,
		Notes:     notes,
		Unsynced:  strings.HasPrefix(activeBase, "unsynced_"),
		Recording: true,
		Backup:    "recording",
	}
	out := append([]flights.Summary{active}, sums...)

	writeJSON(w, map[string]any{
		"flights":         out,
		"scanning":        scanning,
		"manifest_age_s":  manifestAge,
		"active_file":     activeBase,
		"airports_loaded": true,
	})
}

func (s *Server) handleFlightsNotes(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var req struct {
		File  string `json:"file"`
		Notes string `json:"notes"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad JSON", http.StatusBadRequest)
		return
	}
	base := req.File
	if base == "" || base != filepath.Base(base) || !strings.HasSuffix(base, ".db") {
		http.Error(w, "bad file name", http.StatusBadRequest)
		return
	}
	if len(req.Notes) > maxNotesLen {
		http.Error(w, "notes too long", http.StatusBadRequest)
		return
	}
	if base == filepath.Base(s.store.Path()) {
		if err := s.store.UpdateNotes(req.Notes); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, map[string]any{"ok": true})
		return
	}
	if _, err := os.Stat(filepath.Join(s.cfg.Get().DBDir, base)); err != nil {
		http.Error(w, "no such flight", http.StatusNotFound)
		return
	}
	if err := s.flightsMgr.UpdateNotes(base, req.Notes); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(w, map[string]any{"ok": true})
}
