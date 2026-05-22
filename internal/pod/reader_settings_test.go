package pod

import (
	"testing"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/sensors"
)

func TestSettingsAttrRecordsFromSavedConfigWithoutHello(t *testing.T) {
	cfg := config.Defaults()
	cfg.Pod.Attrs = map[string]string{
		sensors.JoinIIOAttr("mag", "sampling_frequency"):    "50",
		sensors.JoinIIOAttr("static", "sampling_frequency"): "25",
	}
	holder := config.NewHolder("", cfg)

	reg := sensors.NewRegistry()
	c := New("", nil, nil, nil, reg, holder)

	recs := c.reader.SettingsAttrRecords()
	if len(recs) != 2 {
		t.Fatalf("records: got %d want 2 (%+v)", len(recs), recs)
	}
	byCh := make(map[string]string, len(recs))
	for _, r := range recs {
		byCh[r.Channel] = r.Value
	}
	if byCh["mag"] != "50" || byCh["static"] != "25" {
		t.Fatalf("values: %+v", byCh)
	}

	views := reg.Get(DeviceName)
	if len(views) != 2 {
		t.Fatalf("registry views: got %d want 2 (%+v)", len(views), views)
	}
	for _, v := range views {
		if !v.Writable {
			t.Errorf("channel %q not writable", v.Channel)
		}
		if len(v.Options) == 0 {
			t.Errorf("channel %q missing rate options", v.Channel)
		}
	}
}
