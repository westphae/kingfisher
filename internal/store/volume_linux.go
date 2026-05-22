//go:build linux

package store

import (
	"path/filepath"

	"golang.org/x/sys/unix"
)

func volumeFreeBytes(dbPath string) (int64, error) {
	dir := filepath.Dir(dbPath)
	var st unix.Statfs_t
	if err := unix.Statfs(dir, &st); err != nil {
		return 0, err
	}
	return int64(st.Bavail) * int64(st.Bsize), nil
}
