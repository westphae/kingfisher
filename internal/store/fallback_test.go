package store

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestNextNoTimePath(t *testing.T) {
	dir := t.TempDir()
	if got, want := nextNoTimePath(dir, "n456t"), filepath.Join(dir, "NOTIME_0001_n456t.db"); got != want {
		t.Errorf("empty dir: got %s, want %s", got, want)
	}
	touch(t, filepath.Join(dir, "NOTIME_0007_n456t.db"), 0)
	touch(t, filepath.Join(dir, "NOTIME_0002_other.db"), 0)
	touch(t, filepath.Join(dir, "20260713T020650Z_n456t.db"), 0) // ignored
	touch(t, filepath.Join(dir, "unsynced_0009_n456t.db"), 0)    // legacy scheme: ignored
	if got, want := nextNoTimePath(dir, "n456t"), filepath.Join(dir, "NOTIME_0008_n456t.db"); got != want {
		t.Errorf("seq scan: got %s, want %s", got, want)
	}
}

func TestOpenWithClockTrust(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenWithClockTrust(dir, "n456t", false)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if got, want := filepath.Base(s.Path()), "NOTIME_0001_n456t.db"; got != want {
		t.Errorf("untrusted clock: got %s, want %s", got, want)
	}
	if !s.FallbackNamed() {
		t.Error("untrusted clock: FallbackNamed() = false")
	}
}

func TestRenameFallback(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "NOTIME_0001_n456t.db")
	touch(t, path, 128)

	// No true start learned: keeps the fallback name.
	s := &Store{path: path, fallback: true, tail: "n456t"}
	s.renameFallback()
	if !exists(path) {
		t.Fatalf("renamed without a true start")
	}

	// True start known: renamed to the corrected timestamp name, tail restored.
	ts := time.Date(2026, 7, 13, 4, 5, 6, 0, time.UTC)
	s.SetTrueStart(ts)
	s.renameFallback()
	want := filepath.Join(dir, "20260713T040506Z_n456t.db")
	if exists(path) || !exists(want) {
		t.Fatalf("rename: old exists=%v new exists=%v", exists(path), exists(want))
	}
	if s.Path() != want {
		t.Errorf("Path() = %s, want %s", s.Path(), want)
	}

	// Non-fallback store never renames.
	p2 := filepath.Join(dir, "20260713T050000Z_n456t.db")
	touch(t, p2, 128)
	s2 := &Store{path: p2, tail: "n456t"}
	s2.SetTrueStart(ts)
	s2.renameFallback()
	if !exists(p2) {
		t.Errorf("non-fallback store renamed its DB")
	}

	// Collision: keeps the fallback name rather than clobbering.
	p3 := filepath.Join(dir, "NOTIME_0002_n456t.db")
	touch(t, p3, 64)
	s3 := &Store{path: p3, fallback: true, tail: "n456t"}
	s3.SetTrueStart(ts) // target 20260713T040506Z_n456t.db already exists
	s3.renameFallback()
	if !exists(p3) {
		t.Errorf("collision clobbered: fallback file gone")
	}
	if fi, _ := os.Stat(want); fi.Size() != 128 {
		t.Errorf("collision overwrote target (size %d, want 128)", fi.Size())
	}
}
