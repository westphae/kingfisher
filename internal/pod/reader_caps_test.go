package pod

import (
	"testing"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
)

func TestEnsureCapsFromReadingEnablesSettings(t *testing.T) {
	r := newReader(nil)
	if !r.ensureCapsFromReading(wire.MagReading{XUt: 1, YUt: 2, ZUt: 3}) {
		t.Fatal("expected cap seed")
	}
	recs := r.SettingsAttrRecords()
	if len(recs) != 1 || recs[0].Channel != "mag" {
		t.Fatalf("records: %+v", recs)
	}
}

func TestNewWithLegacyConfigAttrs(t *testing.T) {
	cfg := config.Defaults()
	cfg.Devices = map[string]config.Device{
		"pod": {
			Attrs: map[string]string{
				"in_mag_x_sampling_frequency": "20",
			},
		},
	}
	reg := sensors.NewRegistry()
	_ = New("", nil, nil, nil, reg, config.NewHolder("", cfg))
	views := reg.Get(DeviceName)
	if len(views) == 0 {
		t.Fatalf("registry views empty; attrs API would show no settings")
	}
}
