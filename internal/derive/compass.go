package derive

import (
	"context"
	"fmt"
	"log"
	"math"
	"strings"
	"sync"
	"time"

	"github.com/westphae/magkal/pkg/field"
	"github.com/westphae/magkal/pkg/kalman"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/gps"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/store"
	"github.com/westphae/kingfisher/internal/units"
)

const compassDevice = "compass"

const mpsPerKt = 1.94384

// Engine runs magnetometer calibration and publishes the compass virtual device.
type Engine struct {
	holder *config.Holder

	mu                   sync.Mutex
	kf                   *kalman.Filter
	alignR               field.Mat3
	alignAircraftToEarth field.Mat3
	alignActive          bool
	alignHeading         float64
	alignMethod          string
	alignYawTrueDeg      float64
	prevMode             kalman.Mode
	alignAccelEMA        *vec3EMA
	alignMagEMA          *vec3EMA
	lastAccel            field.Vec3
	hasLastAccel         bool
}

func compassInterval(cfg *config.Config) time.Duration {
	hz := 10.0
	if cfg != nil && cfg.Compass.RateHz > 0 {
		hz = cfg.Compass.RateHz
	}
	return time.Duration(float64(time.Second) / hz)
}

// CompassFromHub starts the compass derive loop and returns the engine for HTTP align.
func CompassFromHub(ctx context.Context, holder *config.Holder, hub *live.Hub, gpsc *gps.Client, buf *store.Buffer) *Engine {
	e := &Engine{holder: holder}
	e.rebuildFilter(holder.Get())
	go e.run(ctx, hub, gpsc, buf)
	return e
}

func (e *Engine) run(ctx context.Context, hub *live.Hub, gpsc *gps.Client, buf *store.Buffer) {
	reload := e.holder.Subscribe()
	interval := compassInterval(e.holder.Get())
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		cfg := e.holder.Get()
		if !cfg.Compass.Enabled {
			select {
			case <-ctx.Done():
				return
			case <-reload:
				continue
			case <-t.C:
				continue
			}
		}
		want := compassInterval(cfg)
		if want != interval {
			t.Reset(want)
			interval = want
		}
		dt := want.Seconds()
		e.mu.Lock()
		if e.alignAccelEMA == nil {
			e.alignAccelEMA = newVec3EMA(dt, 1.0)
			e.alignMagEMA = newVec3EMA(dt, 1.0)
		}
		e.mu.Unlock()

		select {
		case <-ctx.Done():
			return
		case <-reload:
			e.rebuildFilter(e.holder.Get())
			continue
		case <-t.C:
			e.tick(hub, gpsc, buf, dt)
		}
	}
}

func (e *Engine) rebuildFilter(cfg *config.Config) {
	if cfg == nil {
		return
	}
	c := &cfg.Compass
	kalman.SetDebug(c.Kalman.Debug)
	n0 := c.N0UtOrDefault()
	kf := kalman.NewKalmanFilter(3, n0, c.Kalman.SigmaK0, c.Kalman.SigmaK, c.Kalman.SigmaM)
	kf.SetConvergenceThresholds(c.Kalman.MaxSigmaK, c.Kalman.MaxSigmaL)
	sm := c.Kalman.StateMachine
	if sm.Enabled {
		kf.EnableStateMachine(sm.LockHysteresis, sm.NISWindow, sm.NISThreshold)
	}
	if cal := c.Calibration; len(cal.K) == 3 && len(cal.L) == 3 && len(cal.P) == 6 {
		kf.SeedKLWithP(cal.K, cal.L, matrixToKalman(cal.P))
	}
	e.mu.Lock()
	wasAligned := e.alignActive
	e.kf = kf
	e.prevMode = kf.Mode()
	if a := c.Align; field.IsValidRot(a.R) {
		e.alignR = a.R
		e.alignActive = true
		e.alignHeading = a.AlignHeadingDeg
		e.alignYawTrueDeg = a.YawTrueDeg
		if e.alignYawTrueDeg == 0 {
			e.alignYawTrueDeg = a.AlignHeadingDeg
		}
		if field.IsValidRot(a.AircraftToEarthR) {
			e.alignAircraftToEarth = a.AircraftToEarthR
		}
	} else {
		e.alignActive = false
	}
	if m := c.AlignMethod; m == compassAlignWMM || m == compassAlignAccel {
		e.alignMethod = m
	}
	lostAlign := wasAligned && !e.alignActive
	e.mu.Unlock()
	// A config reload that drops a previously-learned alignment leaves the
	// heading on its unaligned sensor value until re-aligned — make that
	// loud so it isn't discovered silently in flight. (UI already flags
	// align_active=0; this is the operator-log counterpart.)
	if lostAlign {
		log.Print("compass: alignment reset by config change — re-align needed (taxi 2-40 kt or manual)")
	}
}

func matrixToKalman(p [][]float64) kalman.Matrix {
	m := make(kalman.Matrix, len(p))
	for i, row := range p {
		m[i] = append([]float64(nil), row...)
	}
	return m
}

func kalmanToMatrix(p kalman.Matrix) [][]float64 {
	out := make([][]float64, len(p))
	for i, row := range p {
		out[i] = append([]float64(nil), row...)
	}
	return out
}

func (e *Engine) tick(hub *live.Hub, gpsc *gps.Client, buf *store.Buffer, dt float64) {
	snap := hub.SnapshotNow()
	fix := gpsc.LastFix()
	cfg := e.holder.Get()
	c := &cfg.Compass

	magName, raw, ok := pickMag(snap, c.Magnetometer)
	if !ok {
		return
	}
	accel, haveAccel := pickAccel(snap, c.AccelDevice, magName)
	if haveAccel {
		e.lastAccel = accel
		e.hasLastAccel = true
	} else if e.hasLastAccel {
		accel = e.lastAccel
		haveAccel = true
	}

	n0Ut := c.N0UtOrDefault()
	if geo, ok := snap.Devices["geo"]; ok {
		if f, ok := geo.Values["field_f_nt"]; ok && f > 0 {
			n0Ut = f / 1000
		}
	}

	e.mu.Lock()
	kf := e.kf
	if kf == nil {
		e.mu.Unlock()
		return
	}
	select {
	case <-kf.Done:
	default:
	}
	kf.U <- kalman.Matrix{{raw.X, raw.Y, raw.Z}}
	kf.Z <- n0Ut * n0Ut
	<-kf.Done
	k := kf.K()
	l := kf.L()
	mode := kf.Mode()
	nis := kf.NIS()
	converged := 0.0
	if kf.Converged() {
		converged = 1
	}
	prevMode := e.prevMode
	e.prevMode = mode
	e.mu.Unlock()

	if prevMode == kalman.ModeCalibrating && mode == kalman.ModeLocked {
		e.persistCalibration(k, l, kf.P())
	}

	magCal := field.ApplyCal(raw, k, l)
	magAircraft := applySensorMount(c, magName, magCal)

	modelDecl := 0.0
	if geo, ok := snap.Devices["geo"]; ok {
		if d, ok := geo.Values["declination"]; ok {
			modelDecl = d
		}
	}

	var smoothMag field.Vec3
	var tryAuto bool
	e.mu.Lock()
	if haveAccel {
		e.alignAccelEMA.update(accel)
		smoothMag = e.alignMagEMA.update(magAircraft)
		tryAuto = !e.alignActive
	}
	alignActive := e.alignActive
	alignR := e.alignR
	alignA2E := e.alignAircraftToEarth
	e.mu.Unlock()
	if tryAuto && haveAccel {
		e.tryAutoAlign(snap, fix, modelDecl, accel, smoothMag)
		alignActive = e.alignActive
		alignR = e.alignR
	}
	alignMethod := e.alignMethod
	if !alignActive {
		alignMethod = effectiveAlignMethod(c, snap, magName)
	}

	inTaxi := inTaxiBand(fix, c.TaxiMinKtOrDefault(), c.TaxiMaxKtOrDefault())
	trackTrue := fix.Track
	if !fix.HasFix {
		trackTrue = math.NaN()
	}

	var headingVehDeg float64
	headingVehOK := false
	if alignActive {
		magVeh := field.ApplyRot(alignR, magAircraft)
		if alignMethod == compassAlignWMM && field.IsValidRot(alignA2E) {
			headingVehDeg = headingTrueFromAircraftToEarth(alignA2E)
			if decl, ok := snap.Devices["geo"]; ok {
				if d, ok := decl.Values["declination"]; ok {
					headingVehDeg = field.HeadingDeg360(headingVehDeg - d)
				}
			}
		} else {
			headingVehDeg = field.HeadingDeg360(field.HeadingFromAligned(magVeh) * 180 / math.Pi)
		}
		headingVehOK = true
	}

	measFull := inTaxi && headingVehOK && fix.HasFix && !math.IsNaN(trackTrue)

	vals := map[string]float64{
		// units.Heading360 maps magkal's [0,360) onto the aviation (0,360]
		// convention (north = 360, never 0).
		"heading_sensor_deg": units.Heading360(field.HeadingSensorDeg(magCal)),
		"filter_mode":        float64(mode),
		"nis":                nis,
		"converged":          converged,
	}
	if headingVehOK {
		vals["heading_mag_deg"] = units.Heading360(headingVehDeg)
		if decl, ok := snap.Devices["geo"]; ok {
			if d, ok := decl.Values["declination"]; ok {
				vals["heading_true_deg"] = units.Heading360(headingVehDeg + d)
			}
		}
	}
	if alignActive {
		vals["align_active"] = 1
	} else {
		vals["align_active"] = 0
	}

	if alignActive && alignMethod == compassAlignWMM {
		bEarth := magAircraft
		if field.IsValidRot(alignA2E) {
			bEarth = field.ApplyRot(alignA2E, magAircraft)
		}
		publishNEDField(vals, bEarth)
	} else {
		var accelForMeas field.Vec3
		if haveAccel {
			accelForMeas = accel
		}
		measured := field.MeasureField(magAircraft, accelForMeas, measFull, headingVehDeg, trackTrue)
		vals["field_f_nt"] = measured.F * 1000
		if measured.H != nil {
			vals["field_h_nt"] = *measured.H * 1000
		}
		if measured.ZDown != nil {
			vals["field_z_nt"] = *measured.ZDown * 1000
		}
		if measured.InclDeg != nil {
			vals["inclination"] = *measured.InclDeg
		}
		if measFull || alignActive {
			if measured.X != nil {
				vals["field_x_nt"] = *measured.X * 1000
			}
			if measured.Y != nil {
				vals["field_y_nt"] = *measured.Y * 1000
			}
		}
	}

	sm := live.Sample{
		Device: compassDevice,
		TsNs:   time.Now().UnixNano(),
		Values: vals,
	}
	hub.Publish(sm)
	if buf != nil {
		buf.Append(sm)
	}
}

// Align captures sensor→vehicle alignment using the latest GPS fix and geo declination.
func (e *Engine) Align(manualHeadingDeg *float64, fix gps.Fix, modelDeclDeg float64, method string, snap live.Snapshot) error {
	if err := alignMethodName(method); err != nil {
		return err
	}
	e.mu.Lock()
	accelEMA := e.alignAccelEMA
	magEMA := e.alignMagEMA
	c := e.holder.Get().Compass
	e.mu.Unlock()
	if accelEMA == nil || !accelEMA.warmed() || !magEMA.warmed() {
		return fmt.Errorf("filter not warmed up yet — wait a few seconds after start")
	}
	accel := accelEMA.last()
	magName, _, _ := pickMag(snap, c.Magnetometer)
	magAircraft := applySensorMount(&c, magName, magEMA.last())
	if method == "" {
		method = effectiveAlignMethod(&c, snap, magName)
	}
	if manualHeadingDeg != nil {
		h := field.WrapDeg(*manualHeadingDeg)
		return e.captureAlign(snap, &c, method, accel, magAircraft, h, modelDeclDeg)
	}
	if !inTaxiBand(fix, c.TaxiMinKtOrDefault(), c.TaxiMaxKtOrDefault()) {
		return fmt.Errorf("GPS align requires ground speed between %.0f and %.0f kt", c.TaxiMinKtOrDefault(), c.TaxiMaxKtOrDefault())
	}
	if !fix.HasFix || math.IsNaN(fix.Track) {
		return fmt.Errorf("no GPS track; supply a manual heading or wait for a 2D+ fix while taxiing")
	}
	h := field.WrapDeg(fix.Track - modelDeclDeg)
	return e.captureAlign(snap, &c, method, accel, magAircraft, h, modelDeclDeg)
}

func (e *Engine) tryAutoAlign(snap live.Snapshot, fix gps.Fix, modelDeclDeg float64, accel, magCal field.Vec3) {
	c := e.holder.Get().Compass
	if !inTaxiBand(fix, c.TaxiMinKtOrDefault(), c.TaxiMaxKtOrDefault()) || !fix.HasFix || math.IsNaN(fix.Track) {
		return
	}
	magName, _, _ := pickMag(snap, c.Magnetometer)
	method := effectiveAlignMethod(&c, snap, magName)
	h := field.WrapDeg(fix.Track - modelDeclDeg)
	_ = e.captureAlign(snap, &c, method, accel, magCal, h, modelDeclDeg)
}

func (e *Engine) captureAlign(snap live.Snapshot, c *config.Compass, method string, cabinAccel, magAircraft field.Vec3, headingMagDeg, modelDeclDeg float64) error {
	yawTrue := field.WrapDeg(headingMagDeg + modelDeclDeg)
	var R field.Mat3
	var A2E field.Mat3
	var err error
	switch method {
	case compassAlignWMM:
		bNed, ok := nedFieldUt(snap)
		if !ok {
			return fmt.Errorf("WMM align needs geo field (GPS fix)")
		}
		A2E, err = solveAircraftToEarthMagDown(magAircraft, bNed)
		R = field.Mat3{{1, 0, 0}, {0, 1, 0}, {0, 0, 1}}
	default:
		R, err = field.BuildAlignRotation(cabinAccel, magAircraft, headingMagDeg*math.Pi/180)
		A2E = field.Mat3{{1, 0, 0}, {0, 1, 0}, {0, 0, 1}}
		method = compassAlignAccel
	}
	if err != nil {
		return fmt.Errorf("build alignment: %w", err)
	}
	e.mu.Lock()
	e.alignR = R
	e.alignAircraftToEarth = A2E
	e.alignActive = true
	e.alignHeading = headingMagDeg
	e.alignMethod = method
	e.alignYawTrueDeg = yawTrue
	e.mu.Unlock()
	if err := e.persistAlign(R, A2E, headingMagDeg, yawTrue, method); err != nil {
		return err
	}
	log.Printf("derive: compass align (%s): heading_mag=%.2f°", method, headingMagDeg)
	return nil
}

func (e *Engine) persistCalibration(k, l []float64, p kalman.Matrix) {
	path := e.holder.Path()
	cur := e.holder.Get()
	cp := *cur
	cp.Compass.Calibration = config.CompassCalibration{
		K: append([]float64(nil), k...),
		L: append([]float64(nil), l...),
		P: kalmanToMatrix(p),
	}
	if err := config.Save(path, &cp); err != nil {
		log.Printf("derive: compass save calibration: %v", err)
		return
	}
	e.holder.Set(&cp)
	log.Printf("derive: compass calibration saved (filter locked)")
}

func (e *Engine) persistAlign(R, aircraftToEarth field.Mat3, headingMagDeg, yawTrueDeg float64, method string) error {
	path := e.holder.Path()
	cur := e.holder.Get()
	cp := *cur
	cp.Compass.Align = config.CompassAlign{
		R:                R,
		AircraftToEarthR: aircraftToEarth,
		AlignHeadingDeg:  headingMagDeg,
		YawTrueDeg:       yawTrueDeg,
	}
	if method != "" {
		cp.Compass.AlignMethod = method
	}
	if err := config.Save(path, &cp); err != nil {
		return err
	}
	e.holder.Set(&cp)
	return nil
}

func inTaxiBand(fix gps.Fix, minKt, maxKt float64) bool {
	if !fix.HasFix {
		return false
	}
	if math.IsNaN(fix.Speed) {
		return false
	}
	kt := fix.Speed * mpsPerKt
	const eps = 1e-6
	return kt+eps >= minKt && kt-eps <= maxKt
}

func pickMag(snap live.Snapshot, want string) (device string, raw field.Vec3, ok bool) {
	if want != "" {
		if sm, have := snap.Devices[want]; have {
			if v, ok := extractMag(sm.Values); ok {
				return want, v, true
			}
		}
		return "", field.Vec3{}, false
	}
	bestName := ""
	bestScore := 0
	var best field.Vec3
	for name, sm := range snap.Devices {
		if name == compassDevice || name == "geo" || name == "ahrs" || name == "gps" || name == "press_alt" {
			continue
		}
		score := magScore(name, sm.Values)
		if score > bestScore {
			if v, ok := extractMag(sm.Values); ok {
				bestScore = score
				bestName = name
				best = v
			}
		}
	}
	return bestName, best, bestScore > 0
}

func magScore(name string, vals map[string]float64) int {
	score := 0
	if _, ok := extractMag(vals); ok {
		score += 3
	}
	low := strings.ToLower(name)
	if strings.Contains(low, "mmc") || strings.Contains(low, "mag") || strings.Contains(low, "icm") {
		score++
	}
	return score
}

func extractMag(vals map[string]float64) (field.Vec3, bool) {
	mx, hx := vals["magn_x"]
	if !hx {
		mx, hx = vals["mag_x_ut"]
	}
	my, hy := vals["magn_y"]
	if !hy {
		my, hy = vals["mag_y_ut"]
	}
	mz, hz := vals["magn_z"]
	if !hz {
		mz, hz = vals["mag_z_ut"]
	}
	if !hx || !hy || !hz {
		return field.Vec3{}, false
	}
	return field.Vec3{X: mx, Y: my, Z: mz}, true
}

func pickAccel(snap live.Snapshot, want, magDevice string) (field.Vec3, bool) {
	if want != "" {
		if sm, ok := snap.Devices[want]; ok {
			return extractAccel(sm.Values)
		}
	}
	if magDevice != "" {
		if sm, ok := snap.Devices[magDevice]; ok {
			if a, ok := extractAccel(sm.Values); ok {
				return a, true
			}
		}
	}
	if _, imu, ok := findIMU(snap); ok {
		return extractAccel(imu.Values)
	}
	return field.Vec3{}, false
}

func extractAccel(vals map[string]float64) (field.Vec3, bool) {
	ax, okx := vals["accel_x"]
	ay, oky := vals["accel_y"]
	az, okz := vals["accel_z"]
	if !okx || !oky || !okz {
		return field.Vec3{}, false
	}
	return field.Vec3{X: ax, Y: ay, Z: az}, true
}

type vec3EMA struct {
	alpha float64
	prev  field.Vec3
	init  bool
}

func newVec3EMA(dt, tau float64) *vec3EMA {
	a := 1.0
	if tau > 0 {
		a = dt / (tau + dt)
	}
	return &vec3EMA{alpha: a}
}

func (f *vec3EMA) update(v field.Vec3) field.Vec3 {
	if !f.init {
		f.prev = v
		f.init = true
		return v
	}
	f.prev.X = f.alpha*v.X + (1-f.alpha)*f.prev.X
	f.prev.Y = f.alpha*v.Y + (1-f.alpha)*f.prev.Y
	f.prev.Z = f.alpha*v.Z + (1-f.alpha)*f.prev.Z
	return f.prev
}

func (f *vec3EMA) warmed() bool { return f.init }

func (f *vec3EMA) last() field.Vec3 { return f.prev }
