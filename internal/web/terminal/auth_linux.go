//go:build linux

package terminal

import (
	"bufio"
	"fmt"
	"os"
	"os/user"
	"strconv"
	"strings"

	"github.com/msteinert/pam"
)

// Authenticate verifies username/password against Linux PAM and returns identity.
func Authenticate(username, password string) (Identity, error) {
	username = strings.TrimSpace(username)
	if username == "" || password == "" {
		return Identity{}, ErrInvalidLogin
	}
	t, err := pam.StartFunc("login", username, func(s pam.Style, msg string) (string, error) {
		switch s {
		case pam.PromptEchoOff:
			return password, nil
		case pam.ErrorMsg, pam.TextInfo:
			return "", nil
		default:
			return "", nil
		}
	})
	if err != nil {
		return Identity{}, fmt.Errorf("pam start: %w", err)
	}
	if err := t.Authenticate(0); err != nil {
		return Identity{}, ErrInvalidLogin
	}
	if err := t.AcctMgmt(0); err != nil {
		return Identity{}, ErrInvalidLogin
	}
	return lookupIdentity(username)
}

// IdentityForUser resolves a Unix account without PAM (public-key login).
func IdentityForUser(username string) (Identity, error) {
	username = strings.TrimSpace(username)
	if username == "" {
		return Identity{}, ErrInvalidLogin
	}
	return lookupIdentity(username)
}

func lookupIdentity(username string) (Identity, error) {
	u, err := user.Lookup(username)
	if err != nil {
		return Identity{}, err
	}
	uid64, err := strconv.ParseUint(u.Uid, 10, 32)
	if err != nil {
		return Identity{}, err
	}
	gid64, err := strconv.ParseUint(u.Gid, 10, 32)
	if err != nil {
		return Identity{}, err
	}
	shell, err := passwdShell(username)
	if err != nil || shell == "" {
		shell = "/bin/bash"
	}
	return Identity{
		Username: username,
		UID:      uint32(uid64),
		GID:      uint32(gid64),
		Home:     u.HomeDir,
		Shell:    shell,
	}, nil
}

func passwdShell(username string) (string, error) {
	f, err := os.Open("/etc/passwd")
	if err != nil {
		return "", err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if line == "" || line[0] == '#' {
			continue
		}
		fields := strings.Split(line, ":")
		if len(fields) < 7 || fields[0] != username {
			continue
		}
		return fields[6], nil
	}
	return "", sc.Err()
}
