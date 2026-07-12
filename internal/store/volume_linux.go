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

func volumeStats(dbPath string) (free, total int64, err error) {
	dir := filepath.Dir(dbPath)
	var st unix.Statfs_t
	if err := unix.Statfs(dir, &st); err != nil {
		return 0, 0, err
	}
	bs := int64(st.Bsize)
	return int64(st.Bavail) * bs, int64(st.Blocks) * bs, nil
}
