package sensors

import (
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/westphae/go-iio"
)

const hrtimerConfigRoot = "/sys/kernel/config/iio/triggers/hrtimer"

// kingfisherTriggerPrefix is the configfs name prefix for hrtimers this
// process owns. Other tools (e.g. go-iio examples using "goiio-*") are
// left alone.
const kingfisherTriggerPrefix = "kingfisher-"

// cleanupKingfisherHRTimers unbinds any IIO device still tied to a
// kingfisher-* trigger and removes those configfs entries. Called once at
// sensors.Run startup so a prior crash or SIGKILL does not leave triggers
// behind; best-effort when configfs is absent or read-only.
func cleanupKingfisherHRTimers() {
	infos, err := iio.Discover()
	if err == nil {
		for _, info := range infos {
			d, err := iio.OpenPath(info.Path)
			if err != nil {
				continue
			}
			cur, _ := d.Attr("trigger/current_trigger")
			cur = strings.TrimSpace(cur)
			if strings.HasPrefix(cur, kingfisherTriggerPrefix) {
				_ = d.SetAttr("buffer/enable", "0")
				_ = d.SetAttr("trigger/current_trigger", "\n")
			}
			_ = d.Close()
		}
	}
	if _, err := os.Stat(hrtimerConfigRoot); err != nil {
		return
	}
	ents, err := os.ReadDir(hrtimerConfigRoot)
	if err != nil {
		return
	}
	for _, e := range ents {
		if !e.IsDir() || !strings.HasPrefix(e.Name(), kingfisherTriggerPrefix) {
			continue
		}
		p := filepath.Join(hrtimerConfigRoot, e.Name())
		if err := os.Remove(p); err != nil && !os.IsNotExist(err) {
			log.Printf("sensors: remove orphan hrtimer %s: %v", e.Name(), err)
		}
	}
}

// releaseHRTimer disables buffered capture on dev (if any), then deletes the
// trigger from configfs. Call only after the Buffer is closed (Close unbinds
// current_trigger).
func releaseHRTimer(trig *iio.HRTrigger) {
	if trig == nil {
		return
	}
	if err := trig.Remove(); err != nil {
		log.Printf("sensors: remove hrtimer %s: %v", trig.Name(), err)
	}
}
