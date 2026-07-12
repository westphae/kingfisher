//go:build !linux

package store

import "errors"

func volumeFreeBytes(string) (int64, error) {
	return 0, errors.New("store: volume free space requires linux")
}

func volumeStats(string) (int64, int64, error) {
	return 0, 0, errors.New("store: volume stats requires linux")
}
