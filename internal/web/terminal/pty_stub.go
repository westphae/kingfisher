//go:build !linux

package terminal

import (
	"os"
	"os/exec"
)

// StartShell is unavailable off Linux.
func StartShell(id Identity, cols, rows uint16) (*os.File, *exec.Cmd, error) {
	return nil, nil, ErrNotSupported
}

func resizePTY(f *os.File, cols, rows uint16) error {
	return ErrNotSupported
}
