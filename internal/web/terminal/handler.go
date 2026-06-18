package terminal

import (
	"encoding/base64"
	"encoding/json"
	"html/template"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gorilla/websocket"

	"github.com/westphae/kingfisher/internal/config"
)

// Handler serves /terminal and related API routes.
type Handler struct {
	cfgFn     func() config.Terminal
	sessions  *SessionStore
	challenge *ChallengeStore
	limiter   *loginLimiter
	tpl       *template.Template
	up        websocket.Upgrader
}

// New creates a terminal handler. tpl must include terminal.html.
func New(cfgFn func() config.Terminal, tpl *template.Template) *Handler {
	return &Handler{
		cfgFn:     cfgFn,
		sessions:  NewSessionStore(),
		challenge: NewChallengeStore(),
		limiter:   newLoginLimiter(),
		tpl:       tpl,
		up: websocket.Upgrader{
			// Defence-in-depth on top of session cookies: only accept an
			// upgrade when Origin matches Host (browser path) or is absent
			// (non-browser clients). Without this, a malicious third-party
			// page open in the pilot's browser could open a terminal WS
			// behind their back.
			CheckOrigin: func(r *http.Request) bool {
				origin := r.Header.Get("Origin")
				if origin == "" {
					return true
				}
				u, err := url.Parse(origin)
				if err != nil {
					return false
				}
				return strings.EqualFold(u.Host, r.Host)
			},
		},
	}
}

// Register mounts terminal routes on mux.
func (h *Handler) Register(mux *http.ServeMux) {
	mux.HandleFunc("/terminal", h.handlePage)
	mux.HandleFunc("/api/terminal/auth", h.handleAuth)
	mux.HandleFunc("/api/terminal/challenge", h.handleChallenge)
	mux.HandleFunc("/api/terminal/login", h.handleLogin)
	mux.HandleFunc("/api/terminal/logout", h.handleLogout)
	mux.HandleFunc("/api/terminal/session", h.handleSession)
	mux.HandleFunc("/api/terminal/ws", h.handleWS)
}

func (h *Handler) enabled() bool {
	return h.cfgFn().Enabled
}

func (h *Handler) notFound(w http.ResponseWriter) {
	http.NotFound(w, nil)
}

func (h *Handler) handlePage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if !h.enabled() {
		h.notFound(w)
		return
	}
	if h.tpl == nil {
		http.Error(w, "terminal template missing", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = h.tpl.ExecuteTemplate(w, "terminal.html", nil)
}

type loginRequest struct {
	Username    string `json:"username"`
	Password    string `json:"password"`
	ChallengeID string `json:"challenge_id"`
	Signature   string `json:"signature"` // base64 raw signature
}

func (h *Handler) handleAuth(w http.ResponseWriter, r *http.Request) {
	if !h.enabled() {
		h.notFound(w)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	cfg := h.cfgFn()
	writeJSON(w, map[string]any{
		"pubkey_auth":   cfg.PubkeyAuth(),
		"password_auth": cfg.PasswordAuth(),
		"user":          strings.TrimSpace(cfg.User),
	})
}

func (h *Handler) handleChallenge(w http.ResponseWriter, r *http.Request) {
	if !h.enabled() {
		h.notFound(w)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	cfg := h.cfgFn()
	if !cfg.PubkeyAuth() {
		http.Error(w, "public-key auth not configured", http.StatusNotFound)
		return
	}
	id, message, err := h.challenge.Issue()
	if err != nil {
		http.Error(w, "challenge error", http.StatusInternalServerError)
		return
	}
	writeJSON(w, map[string]any{
		"id":          id,
		"message_b64": base64.StdEncoding.EncodeToString(message),
	})
}

func (h *Handler) handleLogin(w http.ResponseWriter, r *http.Request) {
	if !h.enabled() {
		h.notFound(w)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	ip := clientIP(r)
	if !h.limiter.allow(ip) {
		http.Error(w, "too many login attempts", http.StatusTooManyRequests)
		return
	}
	var req loginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}
	cfg := h.cfgFn()
	var id Identity
	var err error
	if req.ChallengeID != "" && req.Signature != "" {
		id, err = h.loginPubkey(cfg, req)
	} else if req.Username != "" && req.Password != "" {
		id, err = h.loginPassword(cfg, req)
	} else {
		http.Error(w, "login credentials required", http.StatusBadRequest)
		return
	}
	if err != nil {
		h.limiter.recordFailure(ip)
		if err == ErrNotSupported {
			http.Error(w, err.Error(), http.StatusNotImplemented)
			return
		}
		if err == ErrInvalidChallenge {
			http.Error(w, "invalid or expired challenge", http.StatusUnauthorized)
			return
		}
		http.Error(w, "authentication failed", http.StatusUnauthorized)
		return
	}
	h.limiter.reset(ip)
	sess, err := h.sessions.Create(id, cfg.SessionTimeout(), cfg.SessionCap())
	if err != nil {
		if err == ErrSessionFull {
			http.Error(w, "too many active terminal sessions", http.StatusServiceUnavailable)
			return
		}
		http.Error(w, "session error", http.StatusInternalServerError)
		return
	}
	maxAge := int(time.Until(sess.ExpiresAt).Seconds())
	http.SetCookie(w, &http.Cookie{
		Name:     cookieName,
		Value:    sess.Token,
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
		MaxAge:   maxAge,
	})
	writeJSON(w, map[string]any{
		"ok":       true,
		"username": id.Username,
	})
}

func (h *Handler) loginPubkey(cfg config.Terminal, req loginRequest) (Identity, error) {
	if !cfg.PubkeyAuth() {
		return Identity{}, ErrInvalidLogin
	}
	message, err := h.challenge.Consume(req.ChallengeID)
	if err != nil {
		return Identity{}, err
	}
	sig, err := base64.StdEncoding.DecodeString(req.Signature)
	if err != nil {
		return Identity{}, ErrInvalidLogin
	}
	ok, err := VerifyAuthorizedSignature(cfg.AuthorizedKeys, message, sig)
	if err != nil {
		return Identity{}, err
	}
	if !ok {
		return Identity{}, ErrInvalidLogin
	}
	return IdentityForUser(cfg.User)
}

func (h *Handler) loginPassword(cfg config.Terminal, req loginRequest) (Identity, error) {
	if !cfg.PasswordAuth() {
		return Identity{}, ErrInvalidLogin
	}
	return Authenticate(req.Username, req.Password)
}

func (h *Handler) handleLogout(w http.ResponseWriter, r *http.Request) {
	if !h.enabled() {
		h.notFound(w)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if c, err := r.Cookie(cookieName); err == nil {
		h.sessions.Delete(c.Value)
	}
	http.SetCookie(w, &http.Cookie{
		Name:     cookieName,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteStrictMode,
		MaxAge:   -1,
	})
	writeJSON(w, map[string]any{"ok": true})
}

func (h *Handler) handleSession(w http.ResponseWriter, r *http.Request) {
	if !h.enabled() {
		h.notFound(w)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	sess, ok := h.sessionFromRequest(r)
	if !ok {
		writeJSON(w, map[string]any{"authenticated": false})
		return
	}
	writeJSON(w, map[string]any{
		"authenticated": true,
		"username":      sess.Identity.Username,
	})
}

func (h *Handler) sessionFromRequest(r *http.Request) (*Session, bool) {
	c, err := r.Cookie(cookieName)
	if err != nil {
		return nil, false
	}
	return h.sessions.Get(c.Value)
}

type wsResize struct {
	Type string `json:"type"`
	Cols int    `json:"cols"`
	Rows int    `json:"rows"`
}

func (h *Handler) handleWS(w http.ResponseWriter, r *http.Request) {
	if !h.enabled() {
		h.notFound(w)
		return
	}
	sess, ok := h.sessionFromRequest(r)
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	conn, err := h.up.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer conn.Close()

	cols, rows := defaultWinsize()
	ptmx, cmd, err := StartShell(sess.Identity, cols, rows)
	if err != nil {
		log.Printf("terminal: shell for %s: %v", sess.Identity.Username, err)
		_ = conn.WriteMessage(websocket.TextMessage, []byte("\r\n*** "+err.Error()+" ***\r\n"))
		return
	}
	defer func() {
		_ = ptmx.Close()
		if cmd.Process != nil {
			_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGHUP)
		}
		_ = cmd.Wait()
	}()

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		buf := make([]byte, 4096)
		for {
			n, err := ptmx.Read(buf)
			if n > 0 {
				if werr := conn.WriteMessage(websocket.BinaryMessage, buf[:n]); werr != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}()

	for {
		mt, msg, err := conn.ReadMessage()
		if err != nil {
			break
		}
		if mt == websocket.TextMessage && len(msg) > 0 && msg[0] == '{' {
			var rz wsResize
			if json.Unmarshal(msg, &rz) == nil && rz.Type == "resize" && rz.Cols > 0 && rz.Rows > 0 {
				_ = resizePTY(ptmx, uint16(rz.Cols), uint16(rz.Rows))
				continue
			}
		}
		if _, err := ptmx.Write(msg); err != nil {
			break
		}
	}
	_ = ptmx.Close()
	wg.Wait()
}

func defaultWinsize() (cols, rows uint16) {
	return 120, 40
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	enc := json.NewEncoder(w)
	_ = enc.Encode(v)
}
