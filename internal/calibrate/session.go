package calibrate

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/westphae/kingfisher/internal/config"
	"github.com/westphae/kingfisher/internal/live"
	"github.com/westphae/kingfisher/internal/sensors"
)

const (
	// Stationary place-and-hold gates (no tip-to-minimize).
	stillAccelVarMax float64 = 0.05 // (m/s²)² mean per-component (~0.22 m/s² RMS)
	stillMagVarMax   float64 = 0.25 // (µT)²
	readyHold                = 2500 * time.Millisecond
	seekHistMax              = 40
)

// Phase is the high-level wizard state.
type Phase string

const (
	PhaseIdle    Phase = "idle"
	PhaseSeeking Phase = "seeking"
	PhaseLocking Phase = "locking"
	PhaseReview  Phase = "review"
)

// SessionState is the JSON snapshot for GET /api/calibrate/session.
type SessionState struct {
	Active             bool                  `json:"active"`
	Target             Target                `json:"target,omitempty"`
	Phase              Phase                 `json:"phase"`
	FaceIndex          int                   `json:"face_index"` // mag guided index; cabin unused
	Face               Face                  `json:"face,omitempty"`
	FaceLabel          string                `json:"face_label,omitempty"`
	FacesTotal         int                   `json:"faces_total"`
	LockDurationS      float64               `json:"lock_duration_s,omitempty"`
	Locked             []Face                `json:"locked"`
	Remaining          []Face                `json:"remaining,omitempty"`
	StatusHint         string                `json:"status_hint,omitempty"`
	Seek               SeekMetrics           `json:"seek"`
	CanLock            bool                  `json:"can_lock"`
	ReadyHoldProgress  float64               `json:"ready_hold_progress,omitempty"`
	ReadyHoldRemaining float64               `json:"ready_hold_remaining_s,omitempty"`
	LockProgress       float64               `json:"lock_progress,omitempty"`
	LockLive           *LockProgressLive     `json:"lock_live,omitempty"`
	IMUFit             *config.IMUCalResult  `json:"imu_fit,omitempty"`
	MagFit             *config.MagCalResult  `json:"mag_fit,omitempty"`
	FaceSamples        map[string]FaceSample `json:"face_samples,omitempty"`
	Saved              *SavedCal             `json:"saved,omitempty"`
	HistoryCabin       *config.IMUCalResult  `json:"history_cabin,omitempty"`
	HistoryPod         *config.MagCalResult  `json:"history_pod,omitempty"`
	Error              string                `json:"error,omitempty"`
}

// SavedCal summarizes last persisted cal for the active target.
type SavedCal struct {
	FittedUTC string `json:"fitted_utc"`
	Summary   string `json:"summary"`
}

// Service owns at most one calibration session.
type Service struct {
	hub *live.Hub
	cfg *config.Holder
	reg *sensors.Registry

	mu           sync.Mutex
	active       bool
	target       Target
	phase        Phase
	faceIndex    int // pod mag guided order only
	faces        map[Face]FaceSample
	detected     Face
	detectOK     bool
	dominance    float64
	hist         []vec3
	imuFit       *config.IMUCalResult
	magFit       *config.MagCalResult
	lockProgress float64
	lockLive     LockProgressLive
	readySince   time.Time
	lastErr      string
	cancelLock   context.CancelFunc
	autoLocking  bool
}

func New(hub *live.Hub, cfg *config.Holder, reg *sensors.Registry) *Service {
	return &Service{hub: hub, cfg: cfg, reg: reg, faces: map[Face]FaceSample{}}
}

// Start begins a new calibration session.
func (s *Service) Start(target Target) error {
	if !target.Valid() {
		return fmt.Errorf("calibrate: invalid target %q", target)
	}
	target = target.Normalize()
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cancelLock != nil {
		s.cancelLock()
		s.cancelLock = nil
	}
	s.active = true
	s.target = target
	s.phase = PhaseSeeking
	s.faceIndex = 0
	s.faces = map[Face]FaceSample{}
	s.resetSeekLocked()
	s.imuFit = nil
	s.magFit = nil
	s.lockProgress = 0
	s.lastErr = ""
	s.autoLocking = false
	return nil
}

func (s *Service) resetSeekLocked() {
	s.hist = nil
	s.readySince = time.Time{}
	s.detected = ""
	s.detectOK = false
	s.dominance = 0
}

// Cancel aborts the session.
func (s *Service) Cancel() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cancelLock != nil {
		s.cancelLock()
		s.cancelLock = nil
	}
	s.active = false
	s.phase = PhaseIdle
	s.faces = map[Face]FaceSample{}
	s.imuFit = nil
	s.magFit = nil
	s.lastErr = ""
}

// Retake clears one face (any-order; cabin will re-detect when placed again).
func (s *Service) Retake(face Face) error {
	if !face.Valid() {
		return fmt.Errorf("calibrate: invalid face %q", face)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.active {
		return fmt.Errorf("calibrate: no active session")
	}
	if s.phase == PhaseLocking {
		return fmt.Errorf("calibrate: busy locking")
	}
	delete(s.faces, face)
	if s.target == TargetPodMag {
		for i, f := range Faces {
			if f == face {
				s.faceIndex = i
				break
			}
		}
	}
	s.phase = PhaseSeeking
	s.resetSeekLocked()
	s.imuFit = nil
	s.magFit = nil
	s.lastErr = ""
	s.autoLocking = false
	return nil
}

// TickSeek updates metrics from the latest hub snapshot.
func (s *Service) TickSeek() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.active || s.phase != PhaseSeeking || s.hub == nil || s.autoLocking {
		return
	}
	snap := s.hub.SnapshotNow()
	var v vec3
	var ok bool
	switch s.target {
	case TargetCabinAccel, TargetCabinGyro:
		// Stillness from accel for both cabin procedures.
		v, _, ok = readAccel(snap)
	case TargetPodMag:
		v, ok = readMag(snap)
	}
	if !ok {
		return
	}
	s.hist = append(s.hist, v)
	if len(s.hist) > seekHistMax {
		s.hist = s.hist[len(s.hist)-seekHistMax:]
	}

	switch s.target {
	case TargetCabinAccel:
		face, dom, faceOK := DetectFace(v)
		if faceOK && s.detectOK && face != s.detected {
			s.resetSeekLocked()
			s.hist = []vec3{v}
		}
		s.detected = face
		s.detectOK = faceOK
		s.dominance = dom
		if !faceOK {
			s.readySince = time.Time{}
			return
		}
	case TargetCabinGyro:
		s.detected = FaceStill
		s.detectOK = true
		s.dominance = 1
	case TargetPodMag:
		s.detected = Faces[s.faceIndex]
		s.detectOK = true
		s.dominance = 1
	}
	s.maybeAutoLockLocked()
}

func (s *Service) maybeAutoLockLocked() {
	seek := s.seekLocked()
	ready := s.canLockLocked(seek)
	if !ready {
		s.readySince = time.Time{}
		return
	}
	now := time.Now()
	if s.readySince.IsZero() {
		s.readySince = now
		return
	}
	if now.Sub(s.readySince) < readyHold || s.autoLocking {
		return
	}
	s.autoLocking = true
	go s.runAutoLock()
}

func (s *Service) canLockLocked(seek SeekMetrics) bool {
	if s.phase != PhaseSeeking || !seek.HaveSample || !seek.Still {
		return false
	}
	switch s.target {
	case TargetCabinAccel:
		return seek.FaceOK && !seek.AlreadyLocked
	case TargetCabinGyro:
		return seek.FaceOK && !seek.AlreadyLocked
	default:
		return seek.FaceOK
	}
}

func (s *Service) runAutoLock() {
	err := s.Lock(context.Background())
	s.mu.Lock()
	s.autoLocking = false
	if err != nil && s.active && s.phase == PhaseSeeking {
		s.lastErr = err.Error()
		s.readySince = time.Time{}
	}
	s.mu.Unlock()
}

// State returns a snapshot for the UI.
func (s *Service) State() SessionState {
	s.mu.Lock()
	defer s.mu.Unlock()
	st := SessionState{
		Active:     s.active,
		Phase:      s.phase,
		FacesTotal: len(Faces),
		Error:      s.lastErr,
	}
	if s.cfg != nil {
		c := s.cfg.Get()
		st.HistoryCabin = c.Calibration.CabinIMU
		st.HistoryPod = c.Calibration.PodMag
	}
	if !s.active {
		st.Phase = PhaseIdle
		st.Saved = s.savedSummaryLocked()
		return st
	}
	st.Target = s.target
	st.FacesTotal = s.target.FacesNeeded()
	st.LockDurationS = s.target.LockDuration().Seconds()
	st.FaceIndex = s.faceIndex
	st.LockProgress = s.lockProgress
	if s.phase == PhaseLocking {
		live := s.lockLive
		st.LockLive = &live
	}
	if s.target == TargetCabinGyro {
		if sm, ok := s.faces[FaceStill]; ok {
			st.Locked = []Face{FaceStill}
			st.FaceSamples = map[string]FaceSample{string(FaceStill): sm}
		} else {
			st.Remaining = []Face{FaceStill}
			st.FaceSamples = map[string]FaceSample{}
		}
	} else {
		for _, f := range Faces {
			if _, ok := s.faces[f]; ok {
				st.Locked = append(st.Locked, f)
			} else {
				st.Remaining = append(st.Remaining, f)
			}
		}
		st.FaceSamples = map[string]FaceSample{}
		for f, sm := range s.faces {
			st.FaceSamples[string(f)] = sm
		}
	}
	st.IMUFit = s.imuFit
	st.MagFit = s.magFit
	st.Seek = s.seekLocked()

	switch s.target {
	case TargetCabinAccel:
		if st.Seek.FaceOK {
			st.Face = st.Seek.DetectedFace
			st.FaceLabel = st.Face.Label()
		}
	case TargetCabinGyro:
		st.Face = FaceStill
		st.FaceLabel = FaceStill.Label()
	case TargetPodMag:
		if s.faceIndex >= 0 && s.faceIndex < len(Faces) {
			st.Face = Faces[s.faceIndex]
			st.FaceLabel = st.Face.Label()
		}
	}
	st.StatusHint = s.statusHintLocked(st.Seek)
	st.CanLock = s.canLockLocked(st.Seek)
	if st.CanLock && !s.readySince.IsZero() {
		elapsed := time.Since(s.readySince).Seconds()
		p := elapsed / readyHold.Seconds()
		if p > 1 {
			p = 1
		}
		st.ReadyHoldProgress = p
		rem := readyHold.Seconds() - elapsed
		if rem < 0 {
			rem = 0
		}
		st.ReadyHoldRemaining = rem
	}
	st.Saved = s.savedSummaryLocked()
	return st
}

func (s *Service) statusHintLocked(seek SeekMetrics) string {
	switch s.target {
	case TargetCabinAccel:
		switch {
		case !seek.FaceOK:
			return "Place the case on a face until one axis dominates (~g)."
		case seek.AlreadyLocked:
			return fmt.Sprintf("%s already captured — flip to another face.", seek.DetectedFace)
		case !seek.Still:
			return "Hold still on the table…"
		default:
			return "Still — capturing soon."
		}
	case TargetCabinGyro:
		switch {
		case seek.AlreadyLocked:
			return "Still dwell captured — Accept or Retake."
		case !seek.Still:
			return "Place on the table in any orientation; hold still…"
		default:
			return "Still — long gyro average starting soon."
		}
	default:
		if seek.Still {
			return "Still — hold for auto-capture."
		}
		return "Hold the pod still on this face."
	}
}

func (s *Service) seekLocked() SeekMetrics {
	m := SeekMetrics{}
	if len(s.hist) == 0 {
		return m
	}
	v := s.hist[len(s.hist)-1]
	m.HaveSample = true
	m.Norm = norm3(v)
	m.Variance = variance3(s.hist)

	switch s.target {
	case TargetCabinAccel:
		m.DetectedFace = s.detected
		m.Dominance = s.dominance
		m.FaceOK = s.detectOK
		if s.detectOK {
			_, locked := s.faces[s.detected]
			m.AlreadyLocked = locked
			m.AxisPrimary = v[s.detected.AxisIndex()]
			m.LateralMS2 = lateralResidual(v, s.detected.AxisIndex())
		}
		m.Still = m.Variance <= stillAccelVarMax
	case TargetCabinGyro:
		m.DetectedFace = FaceStill
		m.FaceOK = true
		m.Dominance = 1
		_, locked := s.faces[FaceStill]
		m.AlreadyLocked = locked
		m.Still = m.Variance <= stillAccelVarMax
	case TargetPodMag:
		face := Faces[s.faceIndex]
		m.DetectedFace = face
		m.FaceOK = true
		m.AxisPrimary = 0
		m.Still = m.Variance <= stillMagVarMax
	}
	return m
}

func (s *Service) savedSummaryLocked() *SavedCal {
	if s.cfg == nil {
		return nil
	}
	c := s.cfg.Get()
	switch {
	case s.active && s.target == TargetCabinAccel && c.Calibration.CabinIMU != nil:
		r := c.Calibration.CabinIMU
		return &SavedCal{FittedUTC: r.AccelFittedUTC, Summary: fmt.Sprintf("accel scale≈[%.4f %.4f %.4f] residual RMS %.4f m/s²", r.AccelScale[0], r.AccelScale[1], r.AccelScale[2], r.ResidualRMS)}
	case s.active && s.target == TargetCabinGyro && c.Calibration.CabinIMU != nil:
		r := c.Calibration.CabinIMU
		utc := r.GyroFittedUTC
		if utc == "" {
			utc = r.FittedUTC
		}
		return &SavedCal{FittedUTC: utc, Summary: fmt.Sprintf("gyro bias @ %.1f °C → T_ref OFFUSER", r.TempCalC)}
	case s.active && s.target == TargetPodMag && c.Calibration.PodMag != nil:
		r := c.Calibration.PodMag
		return &SavedCal{FittedUTC: r.FittedUTC, Summary: fmt.Sprintf("‖B‖≈%.1f µT residual RMS %.2f µT", r.MeanNormUT, r.ResidualRMS)}
	case !s.active && c.Calibration.CabinIMU != nil:
		r := c.Calibration.CabinIMU
		return &SavedCal{FittedUTC: r.FittedUTC, Summary: "cabin IMU: " + r.FittedUTC}
	case !s.active && c.Calibration.PodMag != nil:
		r := c.Calibration.PodMag
		return &SavedCal{FittedUTC: r.FittedUTC, Summary: "pod mag: " + r.FittedUTC}
	}
	return nil
}

// Lock starts averaging the current face/dwell (blocking until done or ctx cancel).
func (s *Service) Lock(ctx context.Context) error {
	s.mu.Lock()
	if !s.active {
		s.mu.Unlock()
		return fmt.Errorf("calibrate: no active session")
	}
	if s.phase == PhaseLocking {
		s.mu.Unlock()
		return fmt.Errorf("calibrate: already locking")
	}
	if s.phase != PhaseSeeking {
		s.mu.Unlock()
		return fmt.Errorf("calibrate: not seeking")
	}
	seek := s.seekLocked()
	if !s.canLockLocked(seek) {
		s.mu.Unlock()
		return fmt.Errorf("calibrate: not ready to lock (still=%v face_ok=%v)", seek.Still, seek.FaceOK)
	}
	var face Face
	switch s.target {
	case TargetCabinAccel:
		face = s.detected
	case TargetCabinGyro:
		face = FaceStill
	default:
		face = Faces[s.faceIndex]
	}
	target := s.target
	dur := target.LockDuration()
	s.phase = PhaseLocking
	s.lockProgress = 0
	s.lockLive = LockProgressLive{StillNow: true}
	s.lastErr = ""
	lockCtx, cancel := context.WithCancel(ctx)
	s.cancelLock = cancel
	s.mu.Unlock()

	done := make(chan struct{})
	var live LockProgressLive
	go func() {
		start := time.Now()
		t := time.NewTicker(100 * time.Millisecond)
		defer t.Stop()
		for {
			select {
			case <-done:
				return
			case <-lockCtx.Done():
				return
			case <-t.C:
				p := time.Since(start).Seconds() / dur.Seconds()
				if p > 1 {
					p = 1
				}
				s.mu.Lock()
				s.lockProgress = p
				s.lockLive = live
				s.mu.Unlock()
			}
		}
	}()

	sample, err := averageFace(lockCtx, s.hub, target, face, dur, &live)
	close(done)

	s.mu.Lock()
	defer s.mu.Unlock()
	s.cancelLock = nil
	s.lockProgress = 0
	if err != nil {
		s.phase = PhaseSeeking
		s.lastErr = err.Error()
		return err
	}
	s.faces[face] = sample
	s.resetSeekLocked()

	if len(s.faces) >= target.FacesNeeded() {
		s.phase = PhaseReview
		if err := s.fitLocked(); err != nil {
			s.lastErr = err.Error()
			return err
		}
		return nil
	}

	s.phase = PhaseSeeking
	if s.target == TargetPodMag {
		next := -1
		for i, f := range Faces {
			if _, ok := s.faces[f]; !ok {
				next = i
				break
			}
		}
		if next >= 0 {
			s.faceIndex = next
		}
	}
	return nil
}

func (s *Service) fitLocked() error {
	switch s.target {
	case TargetCabinAccel:
		fit, err := FitAccel(s.faces)
		if err != nil {
			return err
		}
		s.imuFit = fit
	case TargetCabinGyro:
		sm, ok := s.faces[FaceStill]
		if !ok {
			return fmt.Errorf("calibrate: missing still gyro sample")
		}
		fit, err := FitGyroStill(sm)
		if err != nil {
			return err
		}
		s.imuFit = fit
	case TargetPodMag:
		fit, err := FitMag(s.faces)
		if err != nil {
			return err
		}
		s.magFit = fit
	}
	return nil
}

// Fit recomputes coeffs if enough samples are locked.
func (s *Service) Fit() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.active {
		return fmt.Errorf("calibrate: no active session")
	}
	need := s.target.FacesNeeded()
	if len(s.faces) < need {
		return fmt.Errorf("calibrate: need %d samples, have %d", need, len(s.faces))
	}
	s.phase = PhaseReview
	return s.fitLocked()
}

// Save persists the current fit to config + JSON artifact.
func (s *Service) Save() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.active {
		return fmt.Errorf("calibrate: no active session")
	}
	if s.cfg == nil {
		return fmt.Errorf("calibrate: no config holder")
	}
	now := time.Now().UTC().Format(time.RFC3339)
	switch s.target {
	case TargetCabinAccel:
		if s.imuFit == nil {
			if err := s.fitLocked(); err != nil {
				return err
			}
		}
		fit := *s.imuFit
		fit.FittedUTC = now
		if err := PersistAccel(s.cfg, s.reg, &fit, s.faces); err != nil {
			return err
		}
		s.imuFit = &fit
	case TargetCabinGyro:
		if s.imuFit == nil {
			if err := s.fitLocked(); err != nil {
				return err
			}
		}
		fit := *s.imuFit
		fit.FittedUTC = now
		if err := PersistGyro(s.cfg, s.reg, &fit, s.faces); err != nil {
			return err
		}
		s.imuFit = &fit
	case TargetPodMag:
		if s.magFit == nil {
			if err := s.fitLocked(); err != nil {
				return err
			}
		}
		fit := *s.magFit
		fit.FittedUTC = now
		if err := PersistMag(s.cfg, &fit, s.faces); err != nil {
			return err
		}
		s.magFit = &fit
	}
	s.active = false
	s.phase = PhaseIdle
	return nil
}
