package derive

import (
	"context"
	"log"
	"time"

	"github.com/westphae/geomag/pkg/egm96"
	"github.com/westphae/geomag/pkg/wmm"

	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/store"
)

// DeclinationFromGPS samples the latest GPS fix once per second and
// publishes the WMM-derived magnetic declination on the "geo" virtual
// device.
func DeclinationFromGPS(ctx context.Context, gpsc *gps.Client, hub *live.Hub, buf *store.Buffer) {
	model := wmm.Default()
	if model == nil {
		log.Printf("derive: wmm.Default returned nil — declination disabled")
		return
	}
	t := time.NewTicker(time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			fix := gpsc.LastFix()
			if !fix.HasFix {
				continue
			}
			loc := egm96.NewLocationGeodetic(fix.Lat, fix.Lon, fix.AltMSL)
			f, err := model.MagneticField(loc, time.Now())
			if err != nil {
				continue
			}
			sm := live.Sample{
				Device: "geo",
				TsNs:   time.Now().UnixNano(),
				Values: map[string]float64{
					"declination_deg": f.D(),
					"inclination_deg": f.I(),
					"field_h_nt":      f.H(),
					"field_f_nt":      f.F(),
				},
			}
			hub.Publish(sm)
			if buf != nil {
				buf.Append(sm)
			}
		}
	}
}
