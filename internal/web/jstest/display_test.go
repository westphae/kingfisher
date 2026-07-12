package jstest

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/dop251/goja"
)

// newDisplayVM loads display.js and exposes its KFDisplay module (a top-level
// const, so we re-export it onto the window shim to survive across RunString
// calls). display.js is self-contained; no DOM is touched at load time.
func newDisplayVM(t *testing.T) *goja.Runtime {
	t.Helper()
	src, err := os.ReadFile(filepath.Join("..", "static", "display.js"))
	if err != nil {
		t.Fatalf("read display.js: %v", err)
	}
	vm := goja.New()
	if _, err := vm.RunString("var window = {};"); err != nil {
		t.Fatalf("init window: %v", err)
	}
	if _, err := vm.RunString(string(src) + "\nwindow.D = KFDisplay;"); err != nil {
		t.Fatalf("load display.js: %v", err)
	}
	return vm
}

func TestSystemOverviewCells(t *testing.T) {
	vm := newDisplayVM(t)
	cases := map[string]string{
		`window.D.formatOverviewCell('system','supply_v',5.0276,{})`:            "5.03 V",
		`window.D.formatOverviewCell('system','cpu_temp_c',51.8,{})`:            "52°C",
		`window.D.formatOverviewCell('system','undervolt_now',0,{})`:            "✓",
		`window.D.formatOverviewCell('system','undervolt_now',1,{})`:            "⚠",
		`window.D.formatOverviewCell('system','throttled_since_boot',1,{})`:     "⚠",
		`window.D.formatOverviewCell('system','cpu_pct',6.1,{})`:                "6%",
		`window.D.formatOverviewCell('system','disk_free_gb',792,{})`:           "792 GB",
		`window.D.formatOverviewCell('system','cpu_freq_mhz',2400,{})`:          "2.4 GHz",
		`window.D.formatOverviewCell('system','uptime_s',90061,{})`:            "1d 1h",
	}
	for expr, want := range cases {
		if got := evalStr(t, vm, expr); got != want {
			t.Errorf("%s = %q, want %q", expr, got, want)
		}
	}
}

func TestSystemDetailLabelsAndFormat(t *testing.T) {
	vm := newDisplayVM(t)
	if got := evalStr(t, vm, `window.D.channelLabel('system','supply_v')`); got != "Supply (5V rail)" {
		t.Errorf("supply_v label = %q", got)
	}
	if got := evalStr(t, vm, `window.D.formatValue('system','cpu_temp_c',51.8,{}).text`); got != "51.8 °C" {
		t.Errorf("cpu_temp_c value = %q", got)
	}
	if got := evalStr(t, vm, `window.D.formatValue('system','undervolt_since_boot',1,{}).text`); got != "⚠ occurred" {
		t.Errorf("undervolt_since_boot value = %q", got)
	}
	// supply_v sorts first; layout yields three rows.
	if got := evalStr(t, vm, `window.D.sortKeys('system',['cpu_pct','supply_v','fan_rpm'])[0]`); got != "supply_v" {
		t.Errorf("sort first = %q", got)
	}
	if n := evalInt(t, vm, `window.D.overviewLayout('system',{supply_v:5,cpu_temp_c:50,cpu_pct:5,mem_used_pct:9,disk_used_pct:6,fan_rpm:3000,nvme_temp_c:38,uptime_s:100,undervolt_now:0}).subRows.length`); n != 3 {
		t.Errorf("overview rows = %d, want 3", n)
	}
	// Status flags must be excluded from smoothing.
	if got := evalStr(t, vm, `String(window.D.noSmoothChannel('undervolt_now'))`); got != "true" {
		t.Errorf("undervolt_now noSmooth = %q", got)
	}
}
