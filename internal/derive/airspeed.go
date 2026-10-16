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

// ISA sea-level density (kg/m³). Kept for the incompressible cross-check;
// live CAS/TAS use the compressible impact-pressure equation below.
const rho0KgM3 = 1.225

// Dry-air gas constant (J/(kg·K)), shared with density altitude.
const dryAirR = 287.053

const (
	gammaAir = 1.4
	isaP0Pa  = 101325.0
	isaT0K   = 288.15
)

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
	errPitotAbsent             = airspeedZeroError("pitot not present")
	errInsufficientZeroSamples = airspeedZeroError("insufficient pitot samples during zero capture")
)

type airspeedProcessor struct {
	emaDp   float64
	emaInit bool
}

// AirspeedFromHub reads MS4525 differential pressure and static baro every
// 200 ms and publishes calibrated and true airspeed on the "airspeed" virtual
// device. ias_kt is the sea-level ASI equation (no position error). tas_kt is
// the isentropic true airspeed at the static source's temperature.
// Publishing is skipped until pitot data is present (MS4525 connected).
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
					// Floor-suppressed IAS means we are not claiming an
					// airspeed; do not publish a TAS from residual q_c.
					if iasKt > 0 {
						if tas, ok := CalcTASFromQcKt(dpCal, staticPa, oatC); ok {
							vals["tas_kt"] = tas
						}
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

// CalcIASKt returns calibrated airspeed in knots from impact pressure (Pa).
// This is the subsonic airspeed-indicator equation at sea-level ISA: what a
// mechanical ASI reads from q_c. It is not a position-error correction, and
// it is not the incompressible √(2q/ρ₀) which reads high by a knot or two at
// Bonanza cruise. The stored column remains ias_kt.
func CalcIASKt(dpPa float64) float64 {
	v, ok := isentropicSpeedKt(dpPa, isaP0Pa, isaT0K)
	if !ok {
		return 0
	}
	return v
}

// CalcTASFromQcKt returns true airspeed in knots from impact pressure, static
// pressure, and outside air temperature via the subsonic isentropic pitot
// equation at the local speed of sound. Temperature is whatever the caller
// has (pod static_temp_c today); it is not a recovery-corrected OAT.
func CalcTASFromQcKt(qcPa, staticPa, oatC float64) (float64, bool) {
	if !validPressurePa(staticPa) || !validTempC(oatC) {
		return 0, false
	}
	return isentropicSpeedKt(qcPa, staticPa, oatC+273.15)
}

// isentropicSpeedKt is
//
//	V = a √(5 [ (q_c/P + 1)^((γ-1)/γ) − 1 ])
//
// with a = √(γ R T). γ = 1.4, so 2/(γ−1) = 5 and (γ−1)/γ = 2/7.
func isentropicSpeedKt(qcPa, staticPa, tempK float64) (float64, bool) {
	if math.IsNaN(qcPa) || math.IsNaN(staticPa) || math.IsNaN(tempK) || qcPa <= 0 || staticPa <= 0 || tempK <= 0 {
		return 0, false
	}
	inner := math.Pow(qcPa/staticPa+1, (gammaAir-1)/gammaAir) - 1
	if inner <= 0 || math.IsNaN(inner) {
		return 0, false
	}
	a := math.Sqrt(gammaAir * dryAirR * tempK)
	vMps := a * math.Sqrt((2/(gammaAir-1))*inner)
	if math.IsNaN(vMps) || vMps < 0 {
		return 0, false
	}
	return vMps * mpsPerKt, true
}

// CalcTASKt returns the incompressible density-ratio TAS estimate
// TAS ≈ IAS √(ρ₀/ρ). The live airspeed device uses CalcTASFromQcKt instead.
// Kept so an offline comparison against the old recorder formula stays explicit.
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
	tempC = sm.Values[pod.ChAirspeedTemp]
	return dp, tempC, true
}
