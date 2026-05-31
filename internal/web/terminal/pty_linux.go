//go:build linux

package terminal

import (
	"fmt"
	"os"
	"os/exec"
	"syscall"

	"github.com/creack/pty"
)

// StartShell spawns an interactive login shell for id in a new PTY.
func StartShell(id Identity, cols, rows uint16) (*os.File, *exec.Cmd, error) {
	shell := id.Shell
	if shell == "" {
		shell = "/bin/bash"
	}
	home := id.Home
	if home == "" {
		home = "/"
	}
	cmd := exec.Command(shell, "-l")
	cmd.Dir = home
	cmd.Env = append(os.Environ(),
		"TERM=xterm-256color",
		"HOME="+home,
		"USER="+id.Username,
		"SHELL="+shell,
	)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		Setsid: true,
	}
	euid := uint32(syscall.Geteuid())
	if id.UID != euid {
		if euid != 0 {
			return nil, nil, ErrNeedPrivilege
		}
		cmd.SysProcAttr.Credential = &syscall.Credential{
			Uid: id.UID,
			Gid: id.GID,
		}
	}
	ptmx, err := pty.StartWithSize(cmd, &pty.Winsize{Cols: cols, Rows: rows})
	if err != nil {
		return nil, nil, fmt.Errorf("pty start: %w", err)
	}
	return ptmx, cmd, nil
}

func resizePTY(f *os.File, cols, rows uint16) error {
	return pty.Setsize(f, &pty.Winsize{Cols: cols, Rows: rows})
}
