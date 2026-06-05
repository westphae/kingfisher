package config

import (
	"encoding/json"
	"fmt"
	"math"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// DefaultPath resolves to $XDG_CONFIG_HOME/kingfisher/config.json (typically
// $HOME/.config/kingfisher/config.json on Linux). Falls back to a relative
// path under the cwd if the home directory cannot be determined.
func DefaultPath() string {
	dir, err := os.UserConfigDir()
	if err != nil {
		return "kingfisher.json"
	}
	return filepath.Join(dir, "kingfisher", "config.json")
}

// DefaultDBDir resolves to $HOME/kingfisher/flights. We deliberately put the
// flight DBs in a visible directory under $HOME (not $XDG_DATA_HOME) because
// the pilot copies them off the Pi for post-flight analysis.
func DefaultDBDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return "flights"
	}
	return filepath.Join(home, "kingfisher", "flights")
}

type Channel struct {
	Column string `json:"column,omitempty"`
}

type Device struct {
	Enabled  bool    `json:"enabled"`
	SampleHz float64 `json:"sample_hz"`
	// UseBuffer selects kernel IIO buffered capture (hrtimer + /dev/iio:deviceN).
	// Omitted or true: use buffer when the device has scan_elements. Explicit false:
	// legacy per-channel sysfs poll in sensors.runOne.
	UseBuffer *bool              `json:"use_buffer,omitempty"`
	Channels  map[string]Channel `json:"channels,omitempty"`
	Attrs     map[string]string  `json:"attrs,omitempty"`
}

type GPS struct {
	RateHz float64 `json:"rate_hz"`
}

// Clock configures in-flight chrony recovery (auto reselect + manual helper).
type Clock struct {
	AutoResync          *bool  `json:"auto_resync,omitempty"`
	AutoResyncCooldownS int    `json:"auto_resync_cooldown_s,omitempty"`
	AutoResyncMaxTries  int    `json:"auto_resync_max_tries,omitempty"`
	ResyncHelper        string `json:"resync_helper,omitempty"`
}

const (
	DefaultAutoResyncCooldownS = 300
	DefaultAutoResyncMaxTries  = 6
	DefaultResyncHelper        = "/usr/local/bin/kingfisher-resync-time.sh"
)

func (c *Clock) AutoResyncEffective() bool {
	if c == nil || c.AutoResync == nil {
		return true
	}
	return *c.AutoResync
}

func (c *Clock) AutoResyncCooldownDuration() time.Duration {
	if c == nil || c.AutoResyncCooldownS <= 0 {
		return DefaultAutoResyncCooldownS * time.Second
	}
	return time.Duration(c.AutoResyncCooldownS) * time.Second
}

func (c *Clock) AutoResyncMaxAttemptsEffective() int {
	if c == nil || c.AutoResyncMaxTries <= 0 {
		return DefaultAutoResyncMaxTries
	}
	return c.AutoResyncMaxTries
}

func MergeClockDefaults(c *Clock) {
	if c == nil {
		return
	}
	if c.AutoResyncCooldownS <= 0 {
		c.AutoResyncCooldownS = DefaultAutoResyncCooldownS
	}
	if c.AutoResyncMaxTries <= 0 {
		c.AutoResyncMaxTries = DefaultAutoResyncMaxTries
	}
	if strings.TrimSpace(c.ResyncHelper) == "" {
		c.ResyncHelper = DefaultResyncHelper
	}
}

type PressAlt struct {
	KollsmanInHg float64 `json:"kollsman_inhg"`
}

// Airspeed configures pitot processing for the derived airspeed device.
type Airspeed struct {
	// DpZeroPa is subtracted from raw differential pressure before IAS (Pa).
	DpZeroPa float64 `json:"dp_zero_pa,omitempty"`
	// LowSpeedFloorKt zeroes displayed IAS/TAS below this threshold (kt).
	LowSpeedFloorKt float64 `json:"low_speed_floor_kt,omitempty"`
	// EmaEnabled defaults to true when omitted (see EmaEnabledEffective).
	EmaEnabled *bool   `json:"ema_enabled,omitempty"`
	EmaTauS     float64 `json:"ema_tau_s,omitempty"`
}

const (
	DefaultAirspeedLowSpeedFloorKt = 5.0
	DefaultAirspeedEmaTauS         = 0.5
)

func (a *Airspeed) LowSpeedFloorKtOrDefault() float64 {
	if a == nil || math.IsNaN(a.LowSpeedFloorKt) || a.LowSpeedFloorKt < 0 {
		return DefaultAirspeedLowSpeedFloorKt
	}
	return a.LowSpeedFloorKt
}

func (a *Airspeed) EmaTauSOrDefault() float64 {
	if a == nil || math.IsNaN(a.EmaTauS) || a.EmaTauS <= 0 {
		return DefaultAirspeedEmaTauS
	}
	return a.EmaTauS
}

func (a *Airspeed) EmaEnabledEffective() bool {
	if a == nil || a.EmaEnabled == nil {
		return true
	}
	return *a.EmaEnabled
}

// MergePodDefaults fills unset pod fields from Defaults().
func MergePodDefaults(p *Pod) {
	if p == nil {
		return
	}
	if p.BatteryCapacityMah == 0 {
		p.BatteryCapacityMah = DefaultPodBatteryCapacityMah
	}
}

func MergeAirspeedDefaults(a *Airspeed) {
	if a == nil {
		return
	}
	def := Defaults().Airspeed
	if math.IsNaN(a.LowSpeedFloorKt) || a.LowSpeedFloorKt < 0 {
		a.LowSpeedFloorKt = def.LowSpeedFloorKt
	}
	if math.IsNaN(a.EmaTauS) || a.EmaTauS <= 0 {
		a.EmaTauS = def.EmaTauS
	}
}

type AHRS struct {
	Enabled bool    `json:"enabled"`
	RateHz  float64 `json:"rate_hz"`
}

// CompassKalman holds EKF parameters for magnetometer calibration (magkal).
type CompassKalman struct {
	SigmaK0      float64               `json:"sigma_k0,omitempty"`
	SigmaK       float64               `json:"sigma_k,omitempty"`
	SigmaM       float64               `json:"sigma_m,omitempty"`
	MaxSigmaK    float64               `json:"max_sigma_k,omitempty"`
	MaxSigmaL    float64               `json:"max_sigma_l,omitempty"`
	StateMachine CompassStateMachine   `json:"state_machine,omitempty"`
	// Debug enables magkal per-step EKF log lines (Innovation, Jacobian, gain, ...).
	Debug bool `json:"debug,omitempty"`
}

// CompassStateMachine configures calibrate-then-lock (magkal).
type CompassStateMachine struct {
	Enabled        bool    `json:"enabled,omitempty"`
	LockHysteresis int     `json:"lock_hysteresis,omitempty"`
	NISWindow      int     `json:"nis_window,omitempty"`
	NISThreshold   float64 `json:"nis_threshold,omitempty"`
}

// CompassCalibration persists k, l, and covariance from the EKF.
type CompassCalibration struct {
	K []float64   `json:"k,omitempty"`
	L []float64   `json:"l,omitempty"`
	P [][]float64 `json:"p,omitempty"`
}

// CompassAlign is the sensor→vehicle rotation captured at align time.
type CompassAlign struct {
	R                 [3][3]float64 `json:"r"`
	AircraftToEarthR  [3][3]float64 `json:"aircraft_to_earth_r,omitempty"`
	AlignHeadingDeg   float64       `json:"align_heading_deg"`
	YawTrueDeg        float64       `json:"yaw_true_deg,omitempty"`
}

// Compass configures the derived compass virtual device.
type Compass struct {
	Enabled      bool               `json:"enabled"`
	RateHz       float64            `json:"rate_hz"`
	Magnetometer string             `json:"magnetometer,omitempty"`
	AccelDevice  string             `json:"accel_device,omitempty"`
	// AlignMethod is "wmm" (geomagnetic field + cabin attitude) or "accel"
	// (gravity + mag on one device). Empty defaults from device layout at runtime.
	AlignMethod string `json:"align_method,omitempty"`
	// SensorMountR maps device name -> fixed sensor->aircraft rotation (FRD).
	SensorMountR map[string][3][3]float64 `json:"sensor_mount_r,omitempty"`
	// PodMountR is an optional fixed pod-sensor→fuselage rotation (identity when unset).
	PodMountR    [3][3]float64         `json:"pod_mount_r,omitempty"`
	N0Ut         float64               `json:"n0_ut,omitempty"`
	TaxiMinKt    float64               `json:"taxi_min_kt,omitempty"`
	TaxiMaxKt    float64               `json:"taxi_max_kt,omitempty"`
	Kalman       CompassKalman         `json:"kalman,omitempty"`
	Calibration  CompassCalibration    `json:"calibration,omitempty"`
	Align        CompassAlign          `json:"align,omitempty"`
}

const (
	DefaultCompassN0Ut     = 50.0
	DefaultCompassTaxiMinKt = 2.0
	DefaultCompassTaxiMaxKt = 40.0
)

// CompassKalmanDefaults returns simulation-tuned EKF defaults (µT-scale n0).
func CompassKalmanDefaults() CompassKalman {
	return CompassKalman{
		SigmaK0:   0.25,
		SigmaK:    1e-8,
		SigmaM:    1e-3,
		MaxSigmaK: 1e-2,
		MaxSigmaL: 0.05, // 50 nT when n0 is in µT
		StateMachine: CompassStateMachine{
			Enabled:        false,
			LockHysteresis: 10,
			NISWindow:      100,
			NISThreshold:   4.0,
		},
	}
}

func (c *Compass) TaxiMinKtOrDefault() float64 {
	if c == nil || c.TaxiMinKt <= 0 {
		return DefaultCompassTaxiMinKt
	}
	return c.TaxiMinKt
}

func (c *Compass) TaxiMaxKtOrDefault() float64 {
	if c == nil || c.TaxiMaxKt <= 0 {
		return DefaultCompassTaxiMaxKt
	}
	return c.TaxiMaxKt
}

func (c *Compass) N0UtOrDefault() float64 {
	if c == nil || c.N0Ut <= 0 {
		return DefaultCompassN0Ut
	}
	return c.N0Ut
}

const DefaultKollsmanInHg = 29.92

func boolPtr(v bool) *bool {
	b := v
	return &b
}

// Pod holds wing-pod WiFi/UDP settings and persisted sensor attrs in
// config.json. Firmware build.rs reads wifi_ssid/password/udp_addr.
// Attrs are written on each UI change (config.Save) and survive power-off.
type Pod struct {
	WiFiSSID     string            `json:"wifi_ssid,omitempty"`
	WiFiPassword string            `json:"wifi_password,omitempty"`
	UDPAddr              string            `json:"udp_addr,omitempty"` // Pi host:port pod sends to; kingfisher binds :port on all ifaces
	BatteryCapacityMah     uint16            `json:"battery_capacity_mah,omitempty"`
	Attrs                  map[string]string `json:"attrs,omitempty"` // e.g. in_mag_sampling_frequency
}

const (
	defaultPodUDPAddr            = "192.168.10.1:47808"
	DefaultPodBatteryCapacityMah = 850
)

// PodDeviceName is the registry / UI device id for the wing pod.
const PodDeviceName = "pod"

// PodSettingsDevice returns persisted pod sensor settings for ApplyDeviceConfig.
// pod.attrs is canonical; devices.pod.attrs is merged for older configs.
func (c *Config) PodSettingsDevice() Device {
	attrs := make(map[string]string)
	for k, v := range c.Pod.Attrs {
		attrs[k] = v
	}
	if d, ok := c.Devices[PodDeviceName]; ok {
		for k, v := range d.Attrs {
			if _, have := attrs[k]; !have {
				attrs[k] = v
			}
		}
	}
	enabled := true
	if d, ok := c.Devices[PodDeviceName]; ok {
		enabled = d.Enabled
	}
	return Device{Enabled: enabled, Attrs: attrs}
}

// HowgozitField is one column in a manual log template or flight log schema.
type HowgozitField struct {
	Key       string   `json:"key"`
	Label     string   `json:"label"`
	Type      string   `json:"type"` // number, text, select
	Unit      string   `json:"unit,omitempty"`
	Step      string   `json:"step,omitempty"`       // HTML step for number spinners (e.g. "0.01")
	InputMode string   `json:"input_mode,omitempty"` // decimal, numeric, text
	Options   []string `json:"options,omitempty"`
}

// HowgozitTemplate is a reusable schema for in-flight manual logs.
type HowgozitTemplate struct {
	ID     string          `json:"id"`
	Name   string          `json:"name"`
	Fields []HowgozitField `json:"fields"`
}

// Howgozit configures manual log templates (seeds for + Log). Flight logs live in the DB only.
type Howgozit struct {
	Templates  []HowgozitTemplate `json:"templates,omitempty"`
	ActiveLogs []string           `json:"active_logs,omitempty"` // deprecated; ignored
}

// DefaultHowgozitTemplates returns the built-in ATC Radio and Flight Conditions schemas.
func DefaultHowgozitTemplates() []HowgozitTemplate {
	return []HowgozitTemplate{
		{
			ID:   "atc_radio",
			Name: "ATC Radio",
			Fields: []HowgozitField{
				{Key: "freq_mhz", Label: "Freq", Type: "number", Unit: "MHz"},
				{Key: "facility", Label: "Facility", Type: "text"},
				{Key: "baro_inhg", Label: "Baro", Type: "number", Unit: "inHg", Step: "0.01"},
			},
		},
		{
			ID:   "flight_conditions",
			Name: "Flight Conditions",
			Fields: []HowgozitField{
				{Key: "fuel_used_gal", Label: "Fuel used", Type: "number", Unit: "gal"},
				{Key: "fuel_t1_gal", Label: "T1", Type: "number", Unit: "gal"},
				{Key: "fuel_t2_gal", Label: "T2", Type: "number", Unit: "gal"},
				{Key: "fuel_t3_gal", Label: "T3", Type: "number", Unit: "gal"},
				{Key: "fuel_t4_gal", Label: "T4", Type: "number", Unit: "gal"},
				{Key: "fuel_sel", Label: "Sel", Type: "select", Options: []string{"1", "2", "3", "4"}},
				{Key: "ff_gph", Label: "FF", Type: "number", Unit: "gph"},
				{Key: "mp_inhg", Label: "MP", Type: "number", Unit: "inHg", Step: "0.01"},
				{Key: "rpm", Label: "RPM", Type: "number"},
				{Key: "alt_ft", Label: "Alt", Type: "number", Unit: "ft"},
				{Key: "baro_inhg", Label: "Baro", Type: "number", Unit: "inHg", Step: "0.01"},
				{Key: "oat_c", Label: "OAT", Type: "number", Unit: "°C"},
				{Key: "tit_c", Label: "TIT", Type: "number", Unit: "°C"},
				{Key: "kias", Label: "KIAS", Type: "number", Unit: "kt"},
				{Key: "ktas", Label: "KTAS", Type: "number", Unit: "kt"},
				{Key: "kgs", Label: "KGS", Type: "number", Unit: "kt"},
			},
		},
	}
}

// MergeHowgozitDefaults fills empty howgozit sections from Defaults().
func MergeHowgozitDefaults(h *Howgozit) {
	if h == nil {
		return
	}
	def := Defaults().Howgozit
	if len(h.Templates) == 0 {
		h.Templates = append([]HowgozitTemplate(nil), def.Templates...)
	}
}

// TemplateByID returns the template with the given id, or nil.
func (h *Howgozit) TemplateByID(id string) *HowgozitTemplate {
	if h == nil {
		return nil
	}
	for i := range h.Templates {
		if h.Templates[i].ID == id {
			return &h.Templates[i]
		}
	}
	return nil
}

type Config struct {
	Aircraft     string `json:"aircraft"`
	AircraftName string `json:"aircraft_name,omitempty"`
	Notes        string `json:"notes,omitempty"`
	FlushSeconds int    `json:"flush_seconds"`
	DBDir        string `json:"db_dir"`
	GPSDAddr     string `json:"gpsd_addr"`
	HTTPAddr     string `json:"http_addr"`
	Pod          Pod    `json:"pod,omitempty"`
	// PodUDPAddr is deprecated; use pod.udp_addr. Kept for migration.
	PodUDPAddr string            `json:"pod_udp_addr,omitempty"`
	GPSFields  []string          `json:"gps_fields,omitempty"`
	Devices    map[string]Device `json:"devices,omitempty"`
	GPS        GPS               `json:"gps"`
	Clock      Clock             `json:"clock,omitempty"`
	PressAlt   PressAlt          `json:"press_alt"`
	Airspeed   Airspeed          `json:"airspeed"`
	AHRS       AHRS              `json:"ahrs"`
	Compass    Compass           `json:"compass"`
	Howgozit   Howgozit          `json:"howgozit,omitempty"`
	Terminal   Terminal          `json:"terminal,omitempty"`
}

// Terminal configures the optional browser shell (/terminal).
type Terminal struct {
	Enabled           bool     `json:"enabled"`
	User              string   `json:"user,omitempty"`            // Unix account for shell (required with authorized_keys)
	AuthorizedKeys    []string `json:"authorized_keys,omitempty"` // OpenSSH authorized_keys lines (Ed25519/RSA/ECDSA)
	AllowPassword     bool     `json:"allow_password,omitempty"`  // PAM login when authorized_keys is set
	SessionTimeoutMin int      `json:"session_timeout_min"`
	MaxSessions       int      `json:"max_sessions"`
}

// PubkeyAuth reports whether SSH public-key challenge login is configured.
func (t Terminal) PubkeyAuth() bool {
	if strings.TrimSpace(t.User) == "" {
		return false
	}
	for _, line := range t.AuthorizedKeys {
		s := strings.TrimSpace(line)
		if s != "" && !strings.HasPrefix(s, "#") {
			return true
		}
	}
	return false
}

// PasswordAuth reports whether PAM username/password login is allowed.
func (t Terminal) PasswordAuth() bool {
	if t.PubkeyAuth() {
		return t.AllowPassword
	}
	return true
}

// SessionTimeout returns how long a terminal login session stays valid.
func (t Terminal) SessionTimeout() time.Duration {
	if t.SessionTimeoutMin <= 0 {
		return 8 * time.Hour
	}
	return time.Duration(t.SessionTimeoutMin) * time.Minute
}

// SessionCap returns the concurrent terminal login cap (0 = default 2).
func (t Terminal) SessionCap() int {
	if t.MaxSessions <= 0 {
		return 2
	}
	return t.MaxSessions
}

func Defaults() *Config {
	return &Config{
		Aircraft:     "N12345",
		AircraftName: "Bonanza V35B",
		FlushSeconds: 5,
		DBDir:        DefaultDBDir(),
		GPSDAddr:     "localhost:2947",
		HTTPAddr:     ":8080",
		Pod: Pod{
			WiFiSSID:           "kingfisher",
			WiFiPassword:       "",
			BatteryCapacityMah: DefaultPodBatteryCapacityMah,
		},
		GPSFields: []string{
			"lat", "lon", "alt_msl", "gs", "track", "vs",
			"h_acc", "v_acc", "gs_acc", "vs_acc", "track_acc",
			"fix", "sats",
		},
		Devices:  map[string]Device{},
		GPS:      GPS{RateHz: 5},
		Clock: Clock{
			AutoResync:          boolPtr(true),
			AutoResyncCooldownS: DefaultAutoResyncCooldownS,
			AutoResyncMaxTries:  DefaultAutoResyncMaxTries,
			ResyncHelper:        DefaultResyncHelper,
		},
		PressAlt: PressAlt{KollsmanInHg: DefaultKollsmanInHg},
		Airspeed: Airspeed{
			LowSpeedFloorKt: DefaultAirspeedLowSpeedFloorKt,
			EmaEnabled:      boolPtr(true),
			EmaTauS:         DefaultAirspeedEmaTauS,
		},
		AHRS: AHRS{Enabled: true, RateHz: 20},
		Compass: Compass{
			Enabled:      true,
			RateHz:       10,
			N0Ut:         DefaultCompassN0Ut,
			Kalman:       CompassKalmanDefaults(),
			SensorMountR: map[string][3][3]float64{},
		},
		Howgozit: Howgozit{
			Templates: DefaultHowgozitTemplates(),
		},
		Terminal: Terminal{
			Enabled:           false,
			SessionTimeoutMin: 480,
			MaxSessions:       2,
		},
	}
}

// DeviceOrDefault returns the configured Device entry for `name`, or a
// default-enabled entry with the supplied default rate if absent.
func (c *Config) DeviceOrDefault(name string, defaultHz float64) Device {
	if d, ok := c.Devices[name]; ok {
		return d
	}
	return Device{Enabled: true, SampleHz: defaultHz}
}

// WantBuffer reports whether to use kernel IIO buffered capture for a device
// with the given number of scannable channels.
func (d Device) WantBuffer(scanChannels int) bool {
	if scanChannels == 0 {
		return false
	}
	if d.UseBuffer != nil {
		return *d.UseBuffer
	}
	return true
}

// KollsmanInHg returns the configured altimeter setting in inches of mercury,
// falling back to the standard 29.92 when unset.
func (c *Config) KollsmanInHg() float64 {
	if c == nil || math.IsNaN(c.PressAlt.KollsmanInHg) || c.PressAlt.KollsmanInHg <= 0 {
		return DefaultKollsmanInHg
	}
	return c.PressAlt.KollsmanInHg
}

// PodBatteryCapacityMah returns the LiPo design capacity (mAh) for BQ27441 fallback
// when the gauge has not been programmed or reports zero full capacity.
func (c *Config) PodBatteryCapacityMah() uint16 {
	if c == nil || c.Pod.BatteryCapacityMah == 0 {
		return DefaultPodBatteryCapacityMah
	}
	return c.Pod.BatteryCapacityMah
}

// podUDPConfigAddr returns the configured pod.udp_addr (or legacy/default).
func (c *Config) podUDPConfigAddr() string {
	if c.Pod.UDPAddr != "" {
		return c.Pod.UDPAddr
	}
	if c.PodUDPAddr != "" {
		return c.PodUDPAddr
	}
	return defaultPodUDPAddr
}

// PodListenAddr returns the UDP bind address for kingfisher/podprobe.
// We listen on all interfaces (":port") so datagrams to the AP address
// (192.168.10.1) are received reliably; pod.udp_addr still carries the
// full host:port the firmware sends to. Empty string disables pod ingest.
func (c *Config) PodListenAddr() string {
	_, port, err := net.SplitHostPort(c.podUDPConfigAddr())
	if err != nil || port == "" {
		return ":47808"
	}
	return ":" + port
}

func migratePod(c *Config) {
	if c.Pod.UDPAddr == "" && c.PodUDPAddr != "" {
		c.Pod.UDPAddr = c.PodUDPAddr
	}
	migratePodAttrs(c)
}

func migratePodAttrs(c *Config) {
	if len(c.Pod.Attrs) > 0 {
		return
	}
	if c.Devices == nil {
		return
	}
	d, ok := c.Devices[PodDeviceName]
	if !ok || len(d.Attrs) == 0 {
		return
	}
	c.Pod.Attrs = make(map[string]string, len(d.Attrs))
	for k, v := range d.Attrs {
		c.Pod.Attrs[normalizePodAttrKeyForConfig(k)] = v
	}
}

// normalizePodAttrKeyForConfig rewrites legacy per-channel rate keys to the
// canonical in_{mag,static,airspeed}_sampling_frequency form.
func normalizePodAttrKeyForConfig(key string) string {
	// Avoid import cycle with internal/pod; duplicate the small prefix rule.
	if !strings.HasPrefix(key, "in_") || !strings.HasSuffix(key, "_sampling_frequency") {
		return key
	}
	mid := strings.TrimPrefix(key, "in_")
	mid = strings.TrimSuffix(mid, "_sampling_frequency")
	prefix := mid
	if i := strings.IndexByte(mid, '_'); i > 0 {
		prefix = mid[:i]
	}
	switch prefix {
	case "mag":
		return "in_mag_sampling_frequency"
	case "static":
		return "in_static_sampling_frequency"
	case "airspeed":
		return "in_airspeed_sampling_frequency"
	default:
		return key
	}
}

// Load reads JSON from path; if the file is missing, returns Defaults().
func Load(path string) (*Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return Defaults(), nil
		}
		return nil, fmt.Errorf("config read %s: %w", path, err)
	}
	c := Defaults()
	if err := json.Unmarshal(b, c); err != nil {
		return nil, fmt.Errorf("config parse %s: %w", path, err)
	}
	if c.Devices == nil {
		c.Devices = map[string]Device{}
	}
	migratePod(c)
	MigrateCompassMounts(c)
	ImportMagkalBestFit(&c.Compass)
	MergeCompassKalman(&c.Compass.Kalman)
	MergeAirspeedDefaults(&c.Airspeed)
	MergePodDefaults(&c.Pod)
	MergeClockDefaults(&c.Clock)
	MergeHowgozitDefaults(&c.Howgozit)
	return c, nil
}

// Save atomically writes JSON to path.
func Save(path string, c *Config) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// Holder is a thread-safe wrapper around the current config plus a one-shot
// reload-signal channel that subscribers can re-read after a config update.
// The IIO device-name list is held here too so the web layer can list real
// hardware (vs. derived virtual devices like "ahrs" or "press_alt") without
// re-running discovery.
type Holder struct {
	mu       sync.RWMutex
	cfg      *Config
	path     string
	reloads  []chan struct{}
	iioNames []string
}

func NewHolder(path string, c *Config) *Holder {
	return &Holder{cfg: c, path: path}
}

func (h *Holder) Path() string { return h.path }

func (h *Holder) Get() *Config {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.cfg
}

// Set replaces the live config and signals all subscribers. It does not write
// to disk; callers should Save separately when persistence is desired.
func (h *Holder) Set(c *Config) {
	h.mu.Lock()
	subs := h.reloads
	h.cfg = c
	h.mu.Unlock()
	for _, ch := range subs {
		select {
		case ch <- struct{}{}:
		default:
		}
	}
}

// Subscribe returns a channel that receives a struct{} every time Set is
// called. The channel has a buffer of 1, so missed signals coalesce.
func (h *Holder) Subscribe() <-chan struct{} {
	ch := make(chan struct{}, 1)
	h.mu.Lock()
	h.reloads = append(h.reloads, ch)
	h.mu.Unlock()
	return ch
}

// SetIIODeviceNames records the list of discovered IIO device names so the
// web layer can build a per-device UI without re-walking sysfs. The list is
// kept in discovery order.
func (h *Holder) SetIIODeviceNames(names []string) {
	h.mu.Lock()
	h.iioNames = append([]string(nil), names...)
	h.mu.Unlock()
}

// IIODeviceNames returns the registered IIO device names.
func (h *Holder) IIODeviceNames() []string {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return append([]string(nil), h.iioNames...)
}
