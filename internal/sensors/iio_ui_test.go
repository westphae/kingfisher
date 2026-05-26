package sensors

import (
	"testing"

	"github.com/westphae/kingfisher/internal/config"
)

func TestConfigAttrViews(t *testing.T) {
	views := ConfigAttrViews(config.Device{Enabled: true, SampleHz: 50}, 95)
	if len(views) != 2 {
		t.Fatalf("views: got %d want 2", len(views))
	}
	if views[0].Attr != "sample_hz" || views[0].Value != "50" {
		t.Fatalf("sample_hz: %+v", views[0])
	}
	if views[1].Attr != "enabled" || views[1].Value != "true" {
		t.Fatalf("enabled: %+v", views[1])
	}
	if len(views[0].Options) == 0 {
		t.Fatalf("hz options empty")
	}
	last := views[0].Options[len(views[0].Options)-1]
	if last != "50" && last != "95" {
		t.Fatalf("hz options cap: last=%s opts=%v", last, views[0].Options)
	}
}
