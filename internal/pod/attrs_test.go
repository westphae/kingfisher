package pod

import (
	"testing"

	"github.com/westphae/kingfisher/internal/config"
)

func TestParsePodAttrKey_legacyDataChannels(t *testing.T) {
	cases := []struct {
		key        string
		wantDevice string
		wantAttr   string
	}{
		{"in_mag_x_sampling_frequency", "mmc5983", "sampling_frequency"},
		{"in_static_p_sampling_frequency", "bmp581", "sampling_frequency"},
		{"in_mag_sampling_frequency", "mmc5983", "sampling_frequency"},
		{"in_bmp581_sampling_frequency", "bmp581", "sampling_frequency"},
	}
	for _, tc := range cases {
		dev, attr, ok := parsePodAttrKey(tc.key)
		if !ok || dev != tc.wantDevice || attr != tc.wantAttr {
			t.Errorf("%q -> (%q,%q,%v) want (%q,%q,true)", tc.key, dev, attr, ok, tc.wantDevice, tc.wantAttr)
		}
	}
}

func TestApplyDeviceConfig_legacyKeys(t *testing.T) {
	cmdOut := make(chan outboundCmd, 4)
	r := newReader(cmdOut)
	outs := r.ApplyDeviceConfig(config.Device{
		Attrs: map[string]string{
			"in_mag_x_sampling_frequency":    "20",
			"in_static_p_sampling_frequency": "15",
		},
	})
	if len(outs) != 2 {
		t.Fatalf("cmds: got %d want 2", len(outs))
	}
	recs := r.SettingsAttrRecords()
	if len(recs) < 2 {
		t.Fatalf("records: %+v", recs)
	}
}
