package derive

import (
	"context"
	"math"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/pod"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/store"
)

const airspeedDeviceName = "airspeed"

const chAirspeedDpCal = "airspeed_dp_cal_pa"

// ISA sea-level density (kg/m³) for incompressible pitot IAS.
const rho0KgM3 = 1.225

// Dry-air gas constant (J/(kg·K)) for density from static P and OAT.
const dryAirR = 287.053

const airspeedTick = 200 * time.Millisecond

// AirspeedZeroSampleDuration is how long Zero now averages pitot ΔP before saving.
const AirspeedZeroSampleDuration = 15 * time.Second

const minAirspeedZeroSamples = 50

// SamplePitotDpAverage collects pitot ΔP readings from hub snapshots for duration
// and returns the arithmetic mean. Requires at least minAirspeedZeroSamples.
func SamplePitotDpAverage(ctx context.Context, hub *live.Hub, duration time.Duration) (mean float64, samples int, err error) {
	return samplePitotDpAverageMin(ctx, hub, duration, minAirspeedZeroSamples)
}

func samplePitotDpAverageMin(ctx context.Context, hub *live.Hub, duration time.Duration, minSamples int) (mean float64, samples int, err error) {
	if hub == nil {
		return 0, 0, errPitotAbsent
	}
	dev := pod.DefaultDeviceName(wire.SensorAirspeed)
	ch := hub.Subscribe()
	defer hub.Unsubscribe(ch)

	var sum float64
	deadline := time.Now().Add(duration)
	for time.Now().Before(deadline) {
		remaining := time.Until(deadline)
		select {
		case <-ctx.Done():
			return 0, samples, ctx.Err()
		case snap, ok := <-ch:
			if !ok {
				goto done
			}
			if sm, have := snap.Devices[dev]; have {
				if dp, ok := sm.Values[pod.ChAirspeedDP]; ok && !math.IsNaN(dp) {
					sum += dp
					samples++
				}
			}
		case <-time.After(remaining):
			goto done
		}
	}
done:
	if samples == 0 {
		return 0, 0, errPitotAbsent
	}
	if samples < minSamples {
		return 0, samples, errInsufficientZeroSamples
	}
	return sum / float64(samples), samples, nil
}

type airspeedZeroError string

func (e airspeedZeroError) Error() string { return string(e) }

const (
	errPitotAbsent              = airspeedZeroError("pitot not present")
	errInsufficientZeroSamples  = airspeedZeroError("insufficient pitot samples during zero capture")
)

type airspeedProcessor struct {
	emaDp   float64
	emaInit bool
}

// AirspeedFromHub reads MS4525 differential pressure and static baro every
// 200 ms and publishes indicated/true airspeed on the "airspeed" virtual
// device. Publishing is skipped until pitot data is present (MS4525 connected).
func AirspeedFromHub(ctx context.Context, holder *config.Holder, hub *live.Hub, buf *store.Buffer) {
	t := time.NewTicker(airspeedTick)
	defer t.Stop()
	var proc airspeedProcessor
	settings := airspeedSettingsFrom(holder.Get().Airspeed)
	reload := holder.Subscribe()
	for {
		select {
		case <-ctx.Done():
			return
		case <-reload:
			settings = airspeedSettingsFrom(holder.Get().Airspeed)
			proc.emaInit = false
		case <-t.C:
			snap := hub.SnapshotNow()
			dpPa, pitotTempC, ok := findPitot(snap)
			if !ok {
				continue
			}
			dpCal, iasKt := proc.process(dpPa, settings)
			vals := map[string]float64{
				pod.ChAirspeedDP: dpPa,
				chAirspeedDpCal:  dpCal,
				"ias_kt":         iasKt,
			}
			if validTempC(pitotTempC) {
				vals[pod.ChAirspeedTemp] = pitotTempC
			}
			staticPa, source, haveStatic := findPressurePa(snap)
			if haveStatic {
				vals[pod.ChStaticP] = staticPa
				if oatC, ok := findOATC(snap, source); ok {
					vals[pod.ChStaticTemp] = oatC
					if tas, ok := CalcTASKt(iasKt, staticPa, oatC); ok {
						vals["tas_kt"] = tas
					}
				}
			}
			sm := live.Sample{
				Device: airspeedDeviceName,
				TsNs:   time.Now().UnixNano(),
				Values: vals,
			}
			hub.Publish(sm)
			if buf != nil {
				buf.Append(sm)
			}
		}
	}
}

type airspeedProcSettings struct {
	dpZeroPa        float64
	lowSpeedFloorKt float64
	emaEnabled      bool
	emaTau          time.Duration
}

func airspeedSettingsFrom(a config.Airspeed) airspeedProcSettings {
	return airspeedProcSettings{
		dpZeroPa:        a.DpZeroPa,
		lowSpeedFloorKt: a.LowSpeedFloorKtOrDefault(),
		emaEnabled:      a.EmaEnabledEffective(),
		emaTau:          time.Duration(a.EmaTauSOrDefault() * float64(time.Second)),
	}
}

func (p *airspeedProcessor) process(rawDpPa float64, s airspeedProcSettings) (dpCal, iasKt float64) {
	dp := rawDpPa
	if s.emaEnabled {
		dp = p.emaFilter(rawDpPa, s.emaTau, airspeedTick)
	}
	dpCal = CorrectedDpPa(dp, s.dpZeroPa)
	iasKt = ApplyLowSpeedFloor(CalcIASKt(dpCal), s.lowSpeedFloorKt)
	return dpCal, iasKt
}

func (p *airspeedProcessor) emaFilter(raw float64, tau, dt time.Duration) float64 {
	if !p.emaInit {
		p.emaDp = raw
		p.emaInit = true
		return raw
	}
	alpha := emaAlpha(dt, tau)
	p.emaDp = alpha*raw + (1-alpha)*p.emaDp
	return p.emaDp
}

// emaAlpha returns the smoothing factor for an EMA with time constant tau.
func emaAlpha(dt, tau time.Duration) float64 {
	if tau <= 0 {
		return 1
	}
	return 1 - math.Exp(-float64(dt)/float64(tau))
}

// CorrectedDpPa subtracts a zero offset and clamps to non-negative dynamic pressure.
func CorrectedDpPa(rawDpPa, zeroPa float64) float64 {
	if math.IsNaN(rawDpPa) {
		return 0
	}
	return math.Max(0, rawDpPa-zeroPa)
}

// ApplyLowSpeedFloor returns 0 when IAS is below the display threshold.
func ApplyLowSpeedFloor(iasKt, floorKt float64) float64 {
	if math.IsNaN(iasKt) || iasKt < floorKt {
		return 0
	}
	return iasKt
}

// CalcIASKt returns indicated airspeed in knots from differential pressure (Pa)
// using incompressible flow at ISA sea-level density.
func CalcIASKt(dpPa float64) float64 {
	if math.IsNaN(dpPa) || dpPa <= 0 {
		return 0
	}
	vMps := math.Sqrt(2 * dpPa / rho0KgM3)
	return vMps * mpsPerKt
}

// CalcTASKt returns true airspeed in knots from IAS (kt), static pressure (Pa),
// and outside air temperature (°C) via the density-ratio correction.
func CalcTASKt(iasKt, staticPa, oatC float64) (float64, bool) {
	if math.IsNaN(iasKt) || iasKt <= 0 || !validPressurePa(staticPa) || !validTempC(oatC) {
		return 0, false
	}
	tK := oatC + 273.15
	rho := staticPa / (dryAirR * tK)
	if rho <= 0 || math.IsNaN(rho) {
		return 0, false
	}
	iasMps := iasKt / mpsPerKt
	tasMps := iasMps * math.Sqrt(rho0KgM3/rho)
	return tasMps * mpsPerKt, true
}

func findPitot(s live.Snapshot) (dpPa, tempC float64, ok bool) {
	dev := pod.DefaultDeviceName(wire.SensorAirspeed)
	sm, have := s.Devices[dev]
	if !have {
		return 0, 0, false
	}
	dp, ok := sm.Values[pod.ChAirspeedDP]
	if !ok || math.IsNaN(dp) {
		return 0, 0, false
	}
	tempC, _ = sm.Values[pod.ChAirspeedTemp]
	return dp, tempC, true
}
