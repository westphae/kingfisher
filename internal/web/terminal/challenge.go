package terminal

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"sync"
	"time"
)

const challengeTTL = 60 * time.Second

var ErrInvalidChallenge = errors.New("terminal: invalid or expired challenge")

// ChallengeStore issues one-time login challenges.
type ChallengeStore struct {
	mu    sync.Mutex
	items map[string]*challengeEntry
}

type challengeEntry struct {
	message   []byte
	expiresAt time.Time
}

func NewChallengeStore() *ChallengeStore {
	return &ChallengeStore{items: map[string]*challengeEntry{}}
}

// Issue creates a fresh challenge and returns its id and signed message bytes.
func (s *ChallengeStore) Issue() (id string, message []byte, err error) {
	nonce := make([]byte, 32)
	if _, err := rand.Read(nonce); err != nil {
		return "", nil, err
	}
	idBytes := make([]byte, 16)
	if _, err := rand.Read(idBytes); err != nil {
		return "", nil, err
	}
	id = hex.EncodeToString(idBytes)
	message = BuildChallengeMessage(id, nonce)
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()
	s.purgeLocked(now)
	s.items[id] = &challengeEntry{
		message:   append([]byte(nil), message...),
		expiresAt: now.Add(challengeTTL),
	}
	return id, message, nil
}

// Consume returns the challenge message and removes the entry (one-time use).
func (s *ChallengeStore) Consume(id string) ([]byte, error) {
	if id == "" {
		return nil, ErrInvalidChallenge
	}
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()
	s.purgeLocked(now)
	ent, ok := s.items[id]
	if !ok || !now.Before(ent.expiresAt) {
		return nil, ErrInvalidChallenge
	}
	delete(s.items, id)
	return ent.message, nil
}

func (s *ChallengeStore) purgeLocked(now time.Time) {
	for k, v := range s.items {
		if !now.Before(v.expiresAt) {
			delete(s.items, k)
		}
	}
}
