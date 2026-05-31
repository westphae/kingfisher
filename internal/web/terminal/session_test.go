package terminal

import (
	"testing"
	"time"
)

func TestSessionStoreCreateGetDelete(t *testing.T) {
	store := NewSessionStore()
	id := Identity{Username: "eric", UID: 1000, GID: 1000, Home: "/home/eric", Shell: "/bin/bash"}
	sess, err := store.Create(id, time.Minute, 2)
	if err != nil {
		t.Fatal(err)
	}
	got, ok := store.Get(sess.Token)
	if !ok || got.Identity.Username != "eric" {
		t.Fatalf("get: ok=%v sess=%+v", ok, got)
	}
	store.Delete(sess.Token)
	if _, ok := store.Get(sess.Token); ok {
		t.Fatal("expected session deleted")
	}
}

func TestSessionStoreMaxSessions(t *testing.T) {
	store := NewSessionStore()
	id := Identity{Username: "eric", UID: 1000, GID: 1000}
	if _, err := store.Create(id, time.Minute, 1); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Create(id, time.Minute, 1); err != ErrSessionFull {
		t.Fatalf("err=%v want %v", err, ErrSessionFull)
	}
}

func TestSessionStoreExpiry(t *testing.T) {
	store := NewSessionStore()
	id := Identity{Username: "eric", UID: 1000, GID: 1000}
	sess, err := store.Create(id, 10*time.Millisecond, 2)
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(15 * time.Millisecond)
	if _, ok := store.Get(sess.Token); ok {
		t.Fatal("expected expired session removed")
	}
}
