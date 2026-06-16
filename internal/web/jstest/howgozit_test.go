// Package jstest runs the browser-side howgozit helpers under the goja JS
// engine so the pure data-layer logic (time parsing, legacy-decimal/uppercase
// field normalization, key generation, HTML/attribute escaping) is unit-tested
// without a browser. The source is loaded from ../static/howgozit.js; these
// files live outside static/ so the //go:embed static/* directive in server.go
// never bundles them into the flight binary.
package jstest

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dop251/goja"
)

// newVM loads howgozit.js into a fresh runtime and exposes its private helpers
// as the global T (window.KFHowgozit._test). Only `window` is needed at load
// time (the trailing window.KFHowgozit = ... assignment); every DOM/fetch
// reference in the file lives inside functions these tests never call.
func newVM(t *testing.T) *goja.Runtime {
	t.Helper()
	src, err := os.ReadFile(filepath.Join("..", "static", "howgozit.js"))
	if err != nil {
		t.Fatalf("read howgozit.js: %v", err)
	}
	vm := goja.New()
	if _, err := vm.RunString("var window = {};"); err != nil {
		t.Fatalf("init window: %v", err)
	}
	if _, err := vm.RunString(string(src)); err != nil {
		t.Fatalf("load howgozit.js: %v", err)
	}
	if _, err := vm.RunString("var T = window.KFHowgozit._test;"); err != nil {
		t.Fatalf("bind _test: %v", err)
	}
	return vm
}

// evalStr runs a JS expression and returns its string value, failing on a
// thrown exception.
func evalStr(t *testing.T, vm *goja.Runtime, expr string) string {
	t.Helper()
	v, err := vm.RunString(expr)
	if err != nil {
		t.Fatalf("eval %q: %v", expr, err)
	}
	return v.String()
}

// evalInt runs a JS expression and returns its integer value.
func evalInt(t *testing.T, vm *goja.Runtime, expr string) int64 {
	t.Helper()
	v, err := vm.RunString(expr)
	if err != nil {
		t.Fatalf("eval %q: %v", expr, err)
	}
	return v.ToInteger()
}

func TestNormalizeFieldType(t *testing.T) {
	vm := newVM(t)
	cases := map[string]string{
		"'decimal'": "number", // legacy alias (commit 5a49db4)
		"'numeric'": "number", // legacy alias
		"'number'":  "number",
		"'text'":    "text",
		"'select'":  "select",
		"'NUMBER'":  "number", // case-insensitive
		"'  text '": "text",   // trimmed
		"''":        "number", // empty -> default
		"'garbage'": "number", // unknown -> default
		"null":      "number",
	}
	for in, want := range cases {
		if got := evalStr(t, vm, "T.normalizeFieldType("+in+")"); got != want {
			t.Errorf("normalizeFieldType(%s) = %q, want %q", in, got, want)
		}
	}
}

func TestLegacyInputMode(t *testing.T) {
	vm := newVM(t)
	cases := map[string]string{
		"{type:'decimal'}":                "decimal", // legacy decimal -> decimal inputmode
		"{type:'numeric'}":                "numeric",
		"{type:'decimal', input_mode:''}": "decimal",
		"{type:'number', input_mode:'tel'}": "tel", // explicit wins
		"{type:'text'}":                   "",
		"{type:'number'}":                 "",
	}
	for in, want := range cases {
		if got := evalStr(t, vm, "T.legacyInputMode("+in+")"); got != want {
			t.Errorf("legacyInputMode(%s) = %q, want %q", in, got, want)
		}
	}
}

func TestNormalizeFieldValue(t *testing.T) {
	vm := newVM(t)
	if got := evalStr(t, vm, "T.normalizeFieldValue('atc', {uppercase:true})"); got != "ATC" {
		t.Errorf("uppercase field: got %q, want ATC", got)
	}
	if got := evalStr(t, vm, "T.normalizeFieldValue('atc', {})"); got != "atc" {
		t.Errorf("plain field: got %q, want atc", got)
	}
	// null/undefined must pass through unchanged, not become the string "null".
	if v, err := vm.RunString("T.normalizeFieldValue(null, {uppercase:true}) === null"); err != nil || !v.ToBoolean() {
		t.Errorf("null value should pass through unchanged (err=%v)", err)
	}
}

func TestCloneFieldNormalizesLegacy(t *testing.T) {
	vm := newVM(t)
	// A legacy decimal column must clone to type:number while keeping a
	// decimal inputmode, unit, step, options and the uppercase flag.
	js := `JSON.stringify(T.cloneField({
		key:'baro', label:'Baro', type:'decimal', unit:'inHg', step:'0.01'
	}))`
	got := evalStr(t, vm, js)
	for _, want := range []string{`"type":"number"`, `"input_mode":"decimal"`, `"unit":"inHg"`, `"step":"0.01"`} {
		if !contains(got, want) {
			t.Errorf("cloneField legacy decimal missing %s in %s", want, got)
		}
	}

	sel := evalStr(t, vm, `JSON.stringify(T.cloneField({key:'r',label:'R',type:'select',options:['A','B'],uppercase:true}))`)
	for _, want := range []string{`"type":"select"`, `"options":["A","B"]`, `"uppercase":true`} {
		if !contains(sel, want) {
			t.Errorf("cloneField select missing %s in %s", want, sel)
		}
	}
}

func TestEscapeHtml(t *testing.T) {
	vm := newVM(t)
	// The quote escape is what prevents attribute-injection when values are
	// interpolated into value="..." (see fieldInputHtml).
	cases := map[string]string{
		`'<b>'`:           "&lt;b&gt;",
		`'a & b'`:         "a &amp; b",
		`'\"x\"'`:         "&quot;x&quot;",
		`'plain'`:         "plain",
		"null":           "",
		"undefined":      "",
	}
	for in, want := range cases {
		if got := evalStr(t, vm, "T.escapeHtml("+in+")"); got != want {
			t.Errorf("escapeHtml(%s) = %q, want %q", in, got, want)
		}
	}
}

func TestFieldInputHtmlEscapesValue(t *testing.T) {
	vm := newVM(t)
	// Regression guard for attribute injection / XSS via a user-entered cell
	// value or field key. The malicious payload must be fully escaped.
	js := `T.fieldInputHtml(7, {key:'k"x', label:'L', type:'text'}, '"><script>alert(1)</script>', false)`
	got := evalStr(t, vm, js)
	if contains(got, "<script>") {
		t.Errorf("fieldInputHtml leaked an unescaped <script>: %s", got)
	}
	if !contains(got, "&lt;script&gt;") {
		t.Errorf("fieldInputHtml did not escape the payload: %s", got)
	}
	if !contains(got, "&quot;") {
		t.Errorf("fieldInputHtml did not escape the quote: %s", got)
	}
}

func TestParseTimeToTsNs(t *testing.T) {
	vm := newVM(t)
	// Fixed UTC instant: 2023-11-14 21:15:30 UTC
	const base = "1700000000000000000"

	utcHMS := func(in string) (h, m, s int64) {
		expr := "T.parseTimeToTsNs(" + in + ", " + base + ")"
		h = evalInt(t, vm, "new Date("+expr+"/1e6).getUTCHours()")
		m = evalInt(t, vm, "new Date("+expr+"/1e6).getUTCMinutes()")
		s = evalInt(t, vm, "new Date("+expr+"/1e6).getUTCSeconds()")
		return h, m, s
	}

	for _, c := range []struct {
		in       string
		wantH    int64
		wantMin  int64
		wantSec  int64
	}{
		{"'2115'", 21, 15, 0},
		{"'0915'", 9, 15, 0},
		{"'211530'", 21, 15, 30},
		{"'0000'", 0, 0, 0},
		{"'21:15'", 21, 15, 0},
		{"'21:15:30'", 21, 15, 30},
	} {
		h, m, s := utcHMS(c.in)
		if h != c.wantH || m != c.wantMin || s != c.wantSec {
			t.Errorf("parseTimeToTsNs(%s) -> %02d:%02d:%02d, want %02d:%02d:%02d", c.in, h, m, s, c.wantH, c.wantMin, c.wantSec)
		}
	}

	for _, in := range []string{"'abc'", "'21'", "''", "'  '", "'x:y'", "':'", "'21:'"} {
		v, err := vm.RunString("T.parseTimeToTsNs(" + in + ", " + base + ") === null")
		if err != nil || !v.ToBoolean() {
			t.Errorf("parseTimeToTsNs(%s) should be null (err=%v)", in, err)
		}
	}
}

func TestFormatTimeUTC(t *testing.T) {
	vm := newVM(t)
	const base = "1700000000000000000"
	if got := evalStr(t, vm, "T.formatTime(T.parseTimeToTsNs('2115', "+base+"))"); got != "2115" {
		t.Errorf("formatTime HHMM roundtrip: got %q want 2115", got)
	}
	if got := evalStr(t, vm, "T.formatTime(T.parseTimeToTsNs('211530', "+base+"))"); got != "211530" {
		t.Errorf("formatTime HHMMSS roundtrip: got %q want 211530", got)
	}
}

func TestLabelToKey(t *testing.T) {
	vm := newVM(t)
	cases := map[string]string{
		"'Baro (inHg)'":   "baro_inhg",
		"'  Hello World '": "hello_world",
		"'ATC #1'":        "atc_1",
		"'already_ok'":    "already_ok",
		"'--Trim--'":      "trim",
	}
	for in, want := range cases {
		if got := evalStr(t, vm, "T.labelToKey("+in+")"); got != want {
			t.Errorf("labelToKey(%s) = %q, want %q", in, got, want)
		}
	}
	// Symbols-only / empty must throw rather than yield an empty key.
	for _, in := range []string{"'!!!'", "'   '", "''"} {
		if _, err := vm.RunString("T.labelToKey(" + in + ")"); err == nil {
			t.Errorf("labelToKey(%s) should throw", in)
		}
	}
}

func TestUniqueFieldKey(t *testing.T) {
	vm := newVM(t)
	if got := evalStr(t, vm, "T.uniqueFieldKey('alt', [])"); got != "alt" {
		t.Errorf("no collision: got %q, want alt", got)
	}
	if got := evalStr(t, vm, "T.uniqueFieldKey('alt', [{key:'alt'}])"); got != "alt_2" {
		t.Errorf("one collision: got %q, want alt_2", got)
	}
	if got := evalStr(t, vm, "T.uniqueFieldKey('alt', [{key:'alt'},{key:'alt_2'}])"); got != "alt_3" {
		t.Errorf("two collisions: got %q, want alt_3", got)
	}
}

func TestCellInputType(t *testing.T) {
	vm := newVM(t)
	cases := map[string]string{
		"{type:'select'}":  "select",
		"{type:'text'}":    "text",
		"{type:'number'}":  "number",
		"{type:'decimal'}": "number", // legacy decimal renders as number cell
	}
	for in, want := range cases {
		if got := evalStr(t, vm, "T.cellInputType("+in+")"); got != want {
			t.Errorf("cellInputType(%s) = %q, want %q", in, got, want)
		}
	}
}

func TestNumberFieldAttrs(t *testing.T) {
	vm := newVM(t)
	withStep := evalStr(t, vm, "T.numberFieldAttrs({key:'x', step:'0.1'})")
	if !contains(withStep, `type="number"`) || !contains(withStep, `step="0.1"`) {
		t.Errorf("explicit step should yield number input: %q", withStep)
	}
	// Known key inherits a default step (DEFAULT_NUMBER_STEP).
	defStep := evalStr(t, vm, "T.numberFieldAttrs({key:'baro_inhg'})")
	if !contains(defStep, `type="number"`) || !contains(defStep, `step="0.01"`) {
		t.Errorf("baro_inhg should inherit default step: %q", defStep)
	}
	// No step and unknown key -> free text with a numeric keypad.
	noStep := evalStr(t, vm, "T.numberFieldAttrs({key:'misc'})")
	if !contains(noStep, `type="text"`) || !contains(noStep, `inputmode="decimal"`) {
		t.Errorf("stepless field should be text+decimal keypad: %q", noStep)
	}
}

func TestFieldLabel(t *testing.T) {
	vm := newVM(t)
	if got := evalStr(t, vm, "T.fieldLabel({label:'Baro', unit:'inHg'})"); got != "Baro (inHg)" {
		t.Errorf("with unit: got %q", got)
	}
	if got := evalStr(t, vm, "T.fieldLabel({label:'Notes'})"); got != "Notes" {
		t.Errorf("no unit: got %q", got)
	}
}

func contains(haystack, needle string) bool {
	return strings.Contains(haystack, needle)
}
