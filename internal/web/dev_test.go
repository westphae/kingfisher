package web

import (
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

func TestFindDevWebRoot(t *testing.T) {
	root := FindDevWebRoot(".")
	if root == "" {
		t.Fatal("expected to find internal/web from repo root")
	}
	if !strings.HasSuffix(filepath.ToSlash(root), "internal/web") {
		t.Fatalf("unexpected root: %q", root)
	}
	if FindDevWebRoot("/nonexistent") != "" {
		t.Fatal("expected empty for missing tree")
	}
}

func TestNoCacheHeader(t *testing.T) {
	h := noCache(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/static/app.js", nil))
	if cc := rec.Header().Get("Cache-Control"); cc == "" {
		t.Fatal("expected Cache-Control header")
	}
}
