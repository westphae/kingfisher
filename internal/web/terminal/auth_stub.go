//go:build !linux

package terminal

// Authenticate is unavailable off Linux.
func Authenticate(username, password string) (Identity, error) {
	return Identity{}, ErrNotSupported
}

// IdentityForUser is unavailable off Linux.
func IdentityForUser(username string) (Identity, error) {
	return Identity{}, ErrNotSupported
}
