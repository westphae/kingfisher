package pod

import (
	"testing"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/sensors"
)

func TestSettingsAttrRecordsFromSavedConfigWithoutHello(t *testing.T) {
	cfg := config.Defaults()
	cfg.Pod.Attrs = map[string]string{
		sensors.JoinIIOAttr("mmc5983", "sampling_frequency"): "50",
		sensors.JoinIIOAttr("bmp581", "sampling_frequency"):  "25",
	}
	holder := config.NewHolder("", cfg)

	reg := sensors.NewRegistry()
	c := New("", nil, nil, nil, nil, reg, holder)

	recs := c.reader.SettingsAttrRecords()
	if len(recs) != 2 {
		t.Fatalf("records: got %d want 2 (%+v)", len(recs), recs)
	}
	var vals []string
	for _, r := range recs {
		vals = append(vals, r.Value)
	}
	if !(contains(vals, "50") && contains(vals, "25")) {
		t.Fatalf("values: %+v", recs)
	}

	for _, dev := range []string{"bmp581", "mmc5983"} {
		views := reg.Get(dev)
		if len(views) != 1 {
			t.Fatalf("%s registry views: %+v", dev, views)
		}
		v := views[0]
		if !v.Writable {
			t.Errorf("%s not writable", dev)
		}
		if len(v.Options) == 0 {
			t.Errorf("%s missing rate options", dev)
		}
	}
}

func contains(ss []string, want string) bool {
	for _, s := range ss {
		if s == want {
			return true
		}
	}
	return false
}
