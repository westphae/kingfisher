package sensors

import (
	"testing"

	"github.com/westphae/kingfisher/internal/config"
)

func TestDeviceCaptureEqual(t *testing.T) {
	ub := true
	a := config.Device{
		Enabled:   true,
		SampleHz:  200,
		UseBuffer: &ub,
		Attrs: map[string]string{
			"in_anglvel_x_calibbias": "-0.01",
			"sampling_frequency":     "200",
		},
		Channels: map[string]config.Channel{
			"anglvel_x": {Column: "gx"},
		},
	}
	b := a
	b.Attrs = map[string]string{
		"in_anglvel_x_calibbias": "-0.01",
		"sampling_frequency":     "200",
	}
	b.Channels = map[string]config.Channel{
		"anglvel_x": {Column: "gx"},
	}
	if !deviceCaptureEqual(a, b) {
		t.Fatal("identical devices should be equal")
	}
	b.Attrs["in_anglvel_x_calibbias"] = "0"
	if deviceCaptureEqual(a, b) {
		t.Fatal("attr change should differ")
	}
	b = a
	b.Attrs = map[string]string{
		"in_anglvel_x_calibbias": "-0.01",
		"sampling_frequency":     "200",
	}
	b.Channels = map[string]config.Channel{
		"anglvel_x": {Column: "gx"},
	}
	b.SampleHz = 100
	if deviceCaptureEqual(a, b) {
		t.Fatal("sample_hz change should differ")
	}
}
