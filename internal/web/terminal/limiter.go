package terminal

import (
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

const (
	loginMaxFailures = 5
	loginWindow      = time.Minute
)

type loginLimiter struct {
	mu      sync.Mutex
	attempt map[string]*loginAttempt
}

type loginAttempt struct {
	failures int
	window   time.Time
}

func newLoginLimiter() *loginLimiter {
	return &loginLimiter{attempt: map[string]*loginAttempt{}}
}

func (l *loginLimiter) allow(ip string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	a := l.attempt[ip]
	if a == nil || now.Sub(a.window) > loginWindow {
		return true
	}
	return a.failures < loginMaxFailures
}

func (l *loginLimiter) recordFailure(ip string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	a := l.attempt[ip]
	if a == nil || now.Sub(a.window) > loginWindow {
		l.attempt[ip] = &loginAttempt{failures: 1, window: now}
		return
	}
	a.failures++
}

func (l *loginLimiter) reset(ip string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.attempt, ip)
}

func clientIP(r *http.Request) string {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		if i := strings.IndexByte(xff, ','); i >= 0 {
			return strings.TrimSpace(xff[:i])
		}
		return strings.TrimSpace(xff)
	}
	if xrip := r.Header.Get("X-Real-IP"); xrip != "" {
		return strings.TrimSpace(xrip)
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}
