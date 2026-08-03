package pod

import (
	"testing"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/pod/wire"
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
		{"in_bmp581_oversampling_pressure", "bmp581", AttrOversamplingPressure},
		{"in_bmp581_iir_pressure", "bmp581", AttrIIRPressure},
		{"in_static_oversampling_temp", "bmp581", AttrOversamplingTemp},
		{"in_mmc5983_bandwidth", "mmc5983", AttrBandwidth},
		{"in_mag_bandwidth", "mmc5983", AttrBandwidth},
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

func TestApplyDeviceConfig_bmpOSRIIR(t *testing.T) {
	cmdOut := make(chan outboundCmd, 8)
	r := newReader(cmdOut)
	outs := r.ApplyDeviceConfig(config.Device{
		Attrs: map[string]string{
			"in_bmp581_sampling_frequency":     "25",
			"in_bmp581_oversampling_pressure":  "32",
			"in_bmp581_oversampling_temp":      "2",
			"in_bmp581_iir_pressure":           "3",
			"in_bmp581_iir_temp":               "3",
		},
	})
	if len(outs) != 5 {
		t.Fatalf("cmds: got %d want 5", len(outs))
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	if r.rates[wire.SensorStatic] != 25 || r.bmpOsrPress != 32 || r.bmpIirPress != 3 {
		t.Fatalf("cache: hz=%d osr_p=%d iir_p=%d", r.rates[wire.SensorStatic], r.bmpOsrPress, r.bmpIirPress)
	}
}

func TestApplyDeviceConfig_mmcBandwidth(t *testing.T) {
	cmdOut := make(chan outboundCmd, 4)
	r := newReader(cmdOut)
	outs := r.ApplyDeviceConfig(config.Device{
		Attrs: map[string]string{
			"in_mmc5983_sampling_frequency": "20",
			"in_mmc5983_bandwidth":          "100",
		},
	})
	if len(outs) != 2 {
		t.Fatalf("cmds: got %d want 2", len(outs))
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	if r.rates[wire.SensorMag] != 20 || r.mmcBandwidth != 100 {
		t.Fatalf("cache: hz=%d bw=%d", r.rates[wire.SensorMag], r.mmcBandwidth)
	}
}
