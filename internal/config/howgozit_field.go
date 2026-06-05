package config

import "strings"

// NormalizeHowgozitField canonicalizes field type and legacy aliases.
// Older configs used "decimal" or "numeric" as type instead of input_mode.
func NormalizeHowgozitField(f *HowgozitField) {
	if f == nil {
		return
	}
	t := strings.ToLower(strings.TrimSpace(f.Type))
	switch t {
	case "decimal", "numeric":
		if strings.TrimSpace(f.InputMode) == "" {
			f.InputMode = t
		}
		f.Type = "number"
	case "number", "text", "select":
		f.Type = t
	case "":
		f.Type = "number"
	default:
		f.Type = t
	}
}
