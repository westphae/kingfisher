package web

import (
	"net/http"
	"os"
	"path/filepath"
)

// noCache wraps h and forbids browser caching (hard refresh and normal refresh
// both re-fetch from the server).
func noCache(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
		w.Header().Set("Pragma", "no-cache")
		h.ServeHTTP(w, r)
	})
}

// FindDevWebRoot locates internal/web (contains static/ and templates/) by
// walking upward from dir. Returns "" if not found.
func FindDevWebRoot(startDir string) string {
	dir, err := filepath.Abs(startDir)
	if err != nil {
		return ""
	}
	for {
		candidate := filepath.Join(dir, "internal", "web", "static", "app.css")
		if _, err := os.Stat(candidate); err == nil {
			return filepath.Join(dir, "internal", "web")
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}
