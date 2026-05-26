// Package location tags where a sensor lives: cabin Pi (hub) or wing pod.
package location

const (
	Hub = "hub"
	Pod = "pod"
)

// Valid reports whether s is a known location label.
func Valid(s string) bool {
	return s == Hub || s == Pod
}
