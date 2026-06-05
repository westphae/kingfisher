package config

import "testing"

func TestNormalizeHowgozitField(t *testing.T) {
	t.Run("legacy decimal type", func(t *testing.T) {
		f := HowgozitField{Key: "mp", Type: "decimal", Step: "0.01"}
		NormalizeHowgozitField(&f)
		if f.Type != "number" {
			t.Fatalf("type: got %q want number", f.Type)
		}
		if f.InputMode != "decimal" {
			t.Fatalf("input_mode: got %q want decimal", f.InputMode)
		}
	})
	t.Run("preserves input_mode", func(t *testing.T) {
		f := HowgozitField{Key: "x", Type: "decimal", InputMode: "text"}
		NormalizeHowgozitField(&f)
		if f.Type != "number" || f.InputMode != "text" {
			t.Fatalf("got type=%q input_mode=%q", f.Type, f.InputMode)
		}
	})
	t.Run("empty defaults number", func(t *testing.T) {
		f := HowgozitField{Key: "x"}
		NormalizeHowgozitField(&f)
		if f.Type != "number" {
			t.Fatalf("type: got %q want number", f.Type)
		}
	})
}
