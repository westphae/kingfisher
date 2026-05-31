package terminal

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"sync"
	"time"
)

const cookieName = "kf_terminal"

var (
	ErrSessionFull   = errors.New("terminal: max concurrent sessions reached")
	ErrInvalidLogin  = errors.New("terminal: invalid username or password")
	ErrNotSupported  = errors.New("terminal: authentication not supported on this platform")
	ErrNeedPrivilege = errors.New("terminal: cannot start shell for this user without root or cap_setuid")
)

// Identity is an authenticated Linux user.
type Identity struct {
	Username string
	UID      uint32
	GID      uint32
	Home     string
	Shell    string
}

// Session is a logged-in terminal session.
type Session struct {
	Token     string
	Identity  Identity
	ExpiresAt time.Time
}

// SessionStore holds in-memory login sessions.
type SessionStore struct {
	mu       sync.Mutex
	sessions map[string]*Session
}

func NewSessionStore() *SessionStore {
	return &SessionStore{sessions: map[string]*Session{}}
}

func (s *SessionStore) Create(id Identity, ttl time.Duration, max int) (*Session, error) {
	if ttl <= 0 {
		ttl = 8 * time.Hour
	}
	token, err := newToken()
	if err != nil {
		return nil, err
	}
	now := time.Now()
	sess := &Session{
		Token:     token,
		Identity:  id,
		ExpiresAt: now.Add(ttl),
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.purgeLocked(now)
	if max > 0 && len(s.sessions) >= max {
		return nil, ErrSessionFull
	}
	s.sessions[token] = sess
	return sess, nil
}

func (s *SessionStore) Get(token string) (*Session, bool) {
	if token == "" {
		return nil, false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.purgeLocked(time.Now())
	sess, ok := s.sessions[token]
	if !ok {
		return nil, false
	}
	return sess, true
}

func (s *SessionStore) Delete(token string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.sessions, token)
}

func (s *SessionStore) purgeLocked(now time.Time) {
	for k, v := range s.sessions {
		if !now.Before(v.ExpiresAt) {
			delete(s.sessions, k)
		}
	}
}

func newToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}
