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
	PoweroffHelper      string `json:"poweroff_helper,omitempty"`
}

const (
	DefaultAutoResyncCooldownS = 300
	DefaultAutoResyncMaxTries  = 6
	DefaultResyncHelper        = "/usr/local/bin/kingfisher-resync-time.sh"
	DefaultPoweroffHelper      = "/usr/local/bin/kingfisher-poweroff.sh"
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
	if strings.TrimSpace(c.PoweroffHelper) == "" {
		c.PoweroffHelper = DefaultPoweroffHelper
	}
}

type PressAlt struct {
	KollsmanInHg float64 `json:"kollsman_inhg"`
}

// DisplaySmoothGroup is per-device UI smoothing (browser display only).
type DisplaySmoothGroup struct {
	Mode string  `json:"mode"` // "raw" or "smoothed"
	TauS float64 `json:"tau_s"`
}

// Display holds cockpit presentation settings (not used by derive/store).
type Display struct {
	Smooth map[string]map[string]DisplaySmoothGroup `json:"smooth,omitempty"`
}

// Airspeed configures pitot processing for the derived airspeed device.
type Airspeed struct {
	// DpZeroPa is subtracted from raw differential pressure before IAS (Pa).
	DpZeroPa float64 `json:"dp_zero_pa,omitempty"`
	// LowSpeedFloorKt zeroes displayed IAS/TAS below this threshold (kt).
	LowSpeedFloorKt float64 `json:"low_speed_floor_kt,omitempty"`
	// EmaEnabled defaults to true when omitted (see EmaEnabledEffective).
	EmaEnabled *bool   `json:"ema_enabled,omitempty"`
	EmaTauS    float64 `json:"ema_tau_s,omitempty"`
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

// GDL90 configures Stratux-compatible UDP output for EFB apps (ForeFlight, iFly).
type GDL90 struct {
	Enabled         bool     `json:"enabled"`
	Port            int      `json:"port,omitempty"`
	DHCPLeases      string   `json:"dhcp_leases,omitempty"`
	StaticIPs       []string `json:"static_ips,omitempty"`
	OwnshipHz       float64  `json:"ownship_hz,omitempty"`
	AHRSHz          float64  `json:"ahrs_hz,omitempty"`
	FFAHRSHz        float64  `json:"ff_ahrs_hz,omitempty"`
	HeartbeatHz     float64  `json:"heartbeat_hz,omitempty"`
	OwnshipModeS    string   `json:"ownship_mode_s,omitempty"`
	DeviceShortName string   `json:"device_short_name,omitempty"`
	DeviceLongName  string   `json:"device_long_name,omitempty"`
}

const (
	defaultGDL90Port         = 4000
	defaultGDL90DHCPLeases   = "/var/lib/dhcp/dhcpd.leases"
	defaultGDL90OwnshipHz    = 5.0
	defaultGDL90AHRSHz       = 10.0
	defaultGDL90FFAHRSHz     = 5.0
	defaultGDL90HeartbeatHz  = 1.0
	defaultGDL90OwnshipModeS = "F00000"
	defaultGDL90DeviceShort  = "Kingfisher"
	defaultGDL90DeviceLong   = "kingfisher"
)

func (g *GDL90) PortEffective() int {
	if g == nil || g.Port <= 0 {
		return defaultGDL90Port
	}
	return g.Port
}

func (g *GDL90) DHCPLeasesEffective() string {
	if g == nil || strings.TrimSpace(g.DHCPLeases) == "" {
		return defaultGDL90DHCPLeases
	}
	return g.DHCPLeases
}

func (g *GDL90) OwnshipHzEffective() float64 {
	if g == nil || g.OwnshipHz <= 0 {
		return defaultGDL90OwnshipHz
	}
	return g.OwnshipHz
}

func (g *GDL90) AHRSHzEffective() float64 {
	if g == nil || g.AHRSHz <= 0 {
		return defaultGDL90AHRSHz
	}
	return g.AHRSHz
}

func (g *GDL90) FFAHRSHzEffective() float64 {
	if g == nil || g.FFAHRSHz <= 0 {
		return defaultGDL90FFAHRSHz
	}
	return g.FFAHRSHz
}

func (g *GDL90) HeartbeatHzEffective() float64 {
	if g == nil || g.HeartbeatHz <= 0 {
		return defaultGDL90HeartbeatHz
	}
	return g.HeartbeatHz
}

func (g *GDL90) OwnshipModeSEffective() string {
	if g == nil || strings.TrimSpace(g.OwnshipModeS) == "" {
		return defaultGDL90OwnshipModeS
	}
	return strings.TrimSpace(g.OwnshipModeS)
}

func (g *GDL90) DeviceShortNameEffective() string {
	if g == nil || strings.TrimSpace(g.DeviceShortName) == "" {
		return defaultGDL90DeviceShort
	}
	return truncateRunes(g.DeviceShortName, 8)
}

func (g *GDL90) DeviceLongNameEffective() string {
	if g == nil || strings.TrimSpace(g.DeviceLongName) == "" {
		return defaultGDL90DeviceLong
	}
	return truncateRunes(g.DeviceLongName, 16)
}

func MergeGDL90Defaults(g *GDL90) {
	if g == nil {
		return
	}
	if g.Port <= 0 {
		g.Port = defaultGDL90Port
	}
	if strings.TrimSpace(g.DHCPLeases) == "" {
		g.DHCPLeases = defaultGDL90DHCPLeases
	}
	if g.OwnshipHz <= 0 {
		g.OwnshipHz = defaultGDL90OwnshipHz
	}
	if g.AHRSHz <= 0 {
		g.AHRSHz = defaultGDL90AHRSHz
	}
	if g.FFAHRSHz <= 0 {
		g.FFAHRSHz = defaultGDL90FFAHRSHz
	}
	if g.HeartbeatHz <= 0 {
		g.HeartbeatHz = defaultGDL90HeartbeatHz
	}
	if strings.TrimSpace(g.OwnshipModeS) == "" {
		g.OwnshipModeS = defaultGDL90OwnshipModeS
	}
	if strings.TrimSpace(g.DeviceShortName) == "" {
		g.DeviceShortName = defaultGDL90DeviceShort
	}
	if strings.TrimSpace(g.DeviceLongName) == "" {
		g.DeviceLongName = defaultGDL90DeviceLong
	}
}

func truncateRunes(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// CompassKalman holds EKF parameters for magnetometer calibration (magkal).
type CompassKalman struct {
	SigmaK0      float64             `json:"sigma_k0,omitempty"`
	SigmaK       float64             `json:"sigma_k,omitempty"`
	SigmaM       float64             `json:"sigma_m,omitempty"`
	MaxSigmaK    float64             `json:"max_sigma_k,omitempty"`
	MaxSigmaL    float64             `json:"max_sigma_l,omitempty"`
	StateMachine CompassStateMachine `json:"state_machine,omitempty"`
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
	R                [3][3]float64 `json:"r"`
	AircraftToEarthR [3][3]float64 `json:"aircraft_to_earth_r,omitempty"`
	AlignHeadingDeg  float64       `json:"align_heading_deg"`
	YawTrueDeg       float64       `json:"yaw_true_deg,omitempty"`
}

// Compass configures the derived compass virtual device.
type Compass struct {
	Enabled      bool    `json:"enabled"`
	RateHz       float64 `json:"rate_hz"`
	Magnetometer string  `json:"magnetometer,omitempty"`
	AccelDevice  string  `json:"accel_device,omitempty"`
	// AlignMethod is "wmm" (geomagnetic field + cabin attitude) or "accel"
	// (gravity + mag on one device). Empty defaults from device layout at runtime.
	AlignMethod string `json:"align_method,omitempty"`
	// SensorMountR maps device name -> fixed sensor->aircraft rotation (FRD).
	SensorMountR map[string][3][3]float64 `json:"sensor_mount_r,omitempty"`
	// PodMountR is an optional fixed pod-sensor→fuselage rotation (identity when unset).
	PodMountR   [3][3]float64      `json:"pod_mount_r,omitempty"`
	N0Ut        float64            `json:"n0_ut,omitempty"`
	TaxiMinKt   float64            `json:"taxi_min_kt,omitempty"`
	TaxiMaxKt   float64            `json:"taxi_max_kt,omitempty"`
	Kalman      CompassKalman      `json:"kalman,omitempty"`
	Calibration CompassCalibration `json:"calibration,omitempty"`
	Align       CompassAlign       `json:"align,omitempty"`
}

const (
	DefaultCompassN0Ut      = 50.0
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
	WiFiSSID           string `json:"wifi_ssid,omitempty"`
	WiFiPassword       string `json:"wifi_password,omitempty"`
	UDPAddr            string `json:"udp_addr,omitempty"` // Pi host:port pod sends to; kingfisher binds :port on all ifaces
	BatteryCapacityMah uint16 `json:"battery_capacity_mah,omitempty"`
	// Three-stage power protocol thresholds; consumed by firmware build.rs
	// (compile-time) and by the Pi for burst-quiet link expectations.
	BurstSocPct       uint8             `json:"burst_soc_pct,omitempty"`
	BurstWindowS      uint16            `json:"burst_window_s,omitempty"`
	BurstVoltageUncal float64           `json:"burst_voltage_v_uncalibrated,omitempty"`
	ProtectVoltageV   float64           `json:"protect_voltage_v,omitempty"`
	ProtectSocPct     uint8             `json:"protect_soc_pct,omitempty"`
	LowDebounceS      uint16            `json:"low_debounce_s,omitempty"`
	ModemPowerSave    bool              `json:"modem_power_save,omitempty"` // off: brcmfmac AP drops unicast to dozing STA
	Attrs             map[string]string `json:"attrs,omitempty"` // e.g. in_mag_sampling_frequency
}

const (
	defaultPodUDPAddr            = "192.168.10.1:47808"
	DefaultPodBatteryCapacityMah = 850
	DefaultPodBurstWindowS       = 60
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
	Uppercase bool     `json:"uppercase,omitempty"`  // text: store/display as ALL CAPS; ICAO, etc.
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
		{
			ID:   "atis",
			Name: "ATIS",
			Fields: []HowgozitField{
				{Key: "airport", Label: "Airport", Type: "text", Uppercase: true},
				{Key: "information", Label: "Information", Type: "text", Uppercase: true},
				{Key: "time", Label: "Time", Type: "number", Unit: "Z", Step: "1", InputMode: "numeric"},
				{Key: "wind", Label: "Wind", Type: "text"},
				{Key: "visibility", Label: "Visibility", Type: "number", Unit: "sm", Step: "1", InputMode: "numeric"},
				{Key: "weather", Label: "Weather", Type: "text"},
				{Key: "sky", Label: "Sky Condition", Type: "text"},
				{Key: "temperature", Label: "Temperature", Type: "number", Unit: "C", Step: "1", InputMode: "numeric"},
				{Key: "dewpoint", Label: "Dewpoint", Type: "number", Unit: "C", Step: "1", InputMode: "numeric"},
				{Key: "altimeter", Label: "Altimeter", Type: "number", Unit: "inHg", Step: "0.01", InputMode: "decimal"},
				{Key: "rmk", Label: "Remark", Type: "text"},
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
	Devices    map[string]Device `json:"devices,omitempty"`
	GPS        GPS               `json:"gps"`
	Clock      Clock             `json:"clock,omitempty"`
	PressAlt   PressAlt          `json:"press_alt"`
	Airspeed   Airspeed          `json:"airspeed"`
	AHRS       AHRS              `json:"ahrs"`
	Compass    Compass           `json:"compass"`
	// Calibration holds bench tumble coeffs (Phase 2+). Raw DB samples stay
	// uncorrected; online apply is a separate derive step.
	Calibration Calibration `json:"calibration,omitempty"`
	Howgozit    Howgozit    `json:"howgozit,omitempty"`
	Terminal    Terminal    `json:"terminal,omitempty"`
	Display     Display     `json:"display,omitempty"`
	GDL90       GDL90       `json:"gdl90,omitempty"`
	System      System      `json:"system,omitempty"`
	UPS         UPS         `json:"ups,omitempty"`
	Access      Access      `json:"access,omitempty"`
}

// Calibration stores 6-position tumble results for cabin IMU and/or pod mag,
// plus the cabin gyro temperature shape (GyroTCO).
type Calibration struct {
	CabinIMU *IMUCalResult `json:"cabin_imu,omitempty"`
	PodMag   *MagCalResult `json:"pod_mag,omitempty"`
	// GyroTCO is Δb(T) for the cabin ICM-45686 (°/s, zero at TRefC). Always
	// merged from defaults when absent/invalid so the UI can boldface-correct.
	GyroTCO GyroTCO `json:"gyro_tco,omitempty"`
}

// IMUCalResult is diagonal accel scale/bias plus gyro bias from a tumble.
// Corrected accel: a_corr[i] = AccelScale[i] * (a_raw[i] - AccelBias[i]).
// Same shape as magkal's per-axis k,l (soft/hard iron analogs).
//
// Accel and gyro may be calibrated separately. AccelOffuserApplied /
// GyroOffuserApplied gate soft constant-bias subtract per sensor; AccelScale
// and GyroTCO Δb(T) stay software-side. GyroBias is the still mean at TempCalC;
// GyroBiasAtRef is what was nulled onto the chip (T_ref-baked).
type IMUCalResult struct {
	AccelScale     [3]float64 `json:"accel_scale"`
	AccelBias      [3]float64 `json:"accel_bias"`
	GyroBias       [3]float64 `json:"gyro_bias"`                 // still mean at TempCalC (rad/s)
	GyroBiasAtRef  [3]float64 `json:"gyro_bias_at_ref,omitempty"` // μ − Δb(T_cal); OFFUSER target
	TempCalC       float64    `json:"temp_cal_c,omitempty"`       // mean die °C during six-face
	GyroFaceRMS    [3]float64 `json:"gyro_face_rms,omitempty"`    // per-axis RMS of face means − bias
	GyroOffuser         [3]float64 `json:"gyro_offuser,omitempty"`          // programmed OFFUSER (rad/s)
	AccelOffuser        [3]float64 `json:"accel_offuser,omitempty"`         // programmed OFFUSER (m/s²)
	AccelOffuserApplied bool       `json:"accel_offuser_applied,omitempty"` // accel calibbias programmed
	GyroOffuserApplied  bool       `json:"gyro_offuser_applied,omitempty"`  // gyro calibbias programmed
	// OffuserApplied is true when either accel or gyro OFFUSER was programmed
	// (legacy clients). Prefer AccelOffuserApplied / GyroOffuserApplied.
	OffuserApplied bool    `json:"offuser_applied,omitempty"`
	FittedUTC      string  `json:"fitted_utc"`
	AccelFittedUTC string  `json:"accel_fitted_utc,omitempty"`
	GyroFittedUTC  string  `json:"gyro_fitted_utc,omitempty"`
	ResidualRMS    float64    `json:"residual_rms_ms2,omitempty"` // ‖a_corr‖−g₀ RMS
	MeanNormMS2    float64    `json:"mean_norm_ms2,omitempty"`
	Warnings       []string   `json:"warnings,omitempty"`
}

// MagCalResult is diagonal soft-iron + hard-iron from a pod tumble.
// Corrected: B_corr[i] = SoftIronDiag[i] * (B_raw[i] - HardIron[i]).
type MagCalResult struct {
	SoftIronDiag [3]float64 `json:"soft_iron_diag"`
	HardIron     [3]float64 `json:"hard_iron_ut"`
	FittedUTC    string     `json:"fitted_utc"`
	ResidualRMS  float64    `json:"residual_rms_ut,omitempty"` // ‖B_corr‖ scatter
	MeanNormUT   float64    `json:"mean_norm_ut,omitempty"`
	Warnings     []string   `json:"warnings,omitempty"`
}

// DefaultCalDir is $HOME/kingfisher/calibration for offline JSON artifacts.
func DefaultCalDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return "calibration"
	}
	return filepath.Join(home, "kingfisher", "calibration")
}

// Access gates the root-equivalent endpoints (web terminal, power API) to
// trusted devices. The only untrusted network is the open AP, so the gate
// restricts *only* clients in APSubnet: an AP client is allowed through only
// if its IP is in TrustedIPs; everything off the AP (loopback, home LAN over
// wlan1, Tailscale) is always allowed. Managed from the Settings UI.
type Access struct {
	APSubnet   string            `json:"ap_subnet,omitempty"`   // untrusted AP network (CIDR)
	TrustedIPs []string          `json:"trusted_ips,omitempty"` // AP client IPs/CIDRs allowed through
	Names      map[string]string `json:"names,omitempty"`       // IP → user-supplied device label (ARP has no hostname)
}

const defaultAPSubnet = "192.168.10.0/24"

// APSubnetEffective returns the AP CIDR, defaulting to defaultAPSubnet.
func (a Access) APSubnetEffective() string {
	if strings.TrimSpace(a.APSubnet) == "" {
		return defaultAPSubnet
	}
	return a.APSubnet
}

// System configures the Pi host-telemetry virtual device (`system`): supply
// voltage, throttle/undervoltage flags, CPU temp/load/freq, memory, disk,
// and thermals. It is published to the hub and flight DB like any sensor.
// Enabled defaults to true; RateHz defaults to 1 (host health changes slowly
// and the sticky throttle bits capture sub-second transients regardless).
type System struct {
	Enabled *bool   `json:"enabled,omitempty"`
	RateHz  float64 `json:"rate_hz,omitempty"`
}

const defaultSystemRateHz = 1.0

// EnabledEffective reports whether the system telemetry device runs (default true).
func (s System) EnabledEffective() bool {
	if s.Enabled == nil {
		return true
	}
	return *s.Enabled
}

// RateHzEffective returns the poll rate, defaulting to defaultSystemRateHz.
func (s System) RateHzEffective() float64 {
	if s.RateHz <= 0 {
		return defaultSystemRateHz
	}
	return s.RateHz
}

// UPS configures the Geekworm X1200 UPS HAT monitor (`ups` device): MAX17040
// fuel gauge on I²C bus 1 @0x36, power-loss detect on GPIO6 (1 = external
// power present). GPIO16 (drive high = disable charging) is deliberately
// never claimed. Default off — the HAT is optional hardware.
//
// Kingfisher does not manage power: the x120x kernel driver plus UPower own
// the shutdown decision. PoweroffSocPct only mirrors UPower's
// PercentageAction so the UI can display it and so time-remaining means
// time-until-poweroff. Changing it here changes no behaviour — edit
// /etc/UPower/UPower.conf for that.
type UPS struct {
	Enabled        bool    `json:"enabled"`
	RateHz         float64 `json:"rate_hz,omitempty"`           // default 1, clamped to [0.1, 10]
	PoweroffSocPct float64 `json:"poweroff_soc_pct,omitempty"`  // default 5, matching UPower PercentageAction
}

const (
	defaultUPSRateHz         = 1.0
	defaultUPSPoweroffSocPct = 5.0
)

// RateHzEffective returns the poll rate, defaulting to 1 Hz.
func (u UPS) RateHzEffective() float64 {
	if u.RateHz <= 0 {
		return defaultUPSRateHz
	}
	return math.Min(math.Max(u.RateHz, 0.1), 10)
}

// PoweroffSocEffective returns the SOC at which the driver/UPower will power
// the machine off. Informational only — used to scale time-remaining.
func (u UPS) PoweroffSocEffective() float64 {
	if u.PoweroffSocPct <= 0 {
		return defaultUPSPoweroffSocPct
	}
	return u.PoweroffSocPct
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
		HTTPAddr:     "127.0.0.1:8080", // loopback only; Caddy fronts it on :443 (allowlist would be bypassable on a public bind)
		Access: Access{
			APSubnet:   defaultAPSubnet,
			TrustedIPs: []string{"192.168.10.158", "192.168.10.230"}, // reserved EFBs
		},
		Pod: Pod{
			WiFiSSID:           "kingfisher",
			WiFiPassword:       "",
			BatteryCapacityMah: DefaultPodBatteryCapacityMah,
		},
		Devices: map[string]Device{},
		GPS:     GPS{RateHz: 5},
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
		GDL90: GDL90{
			Enabled:         false,
			Port:            defaultGDL90Port,
			DHCPLeases:      defaultGDL90DHCPLeases,
			OwnshipHz:       defaultGDL90OwnshipHz,
			AHRSHz:          defaultGDL90AHRSHz,
			FFAHRSHz:        defaultGDL90FFAHRSHz,
			HeartbeatHz:     defaultGDL90HeartbeatHz,
			OwnshipModeS:    defaultGDL90OwnshipModeS,
			DeviceShortName: defaultGDL90DeviceShort,
			DeviceLongName:  defaultGDL90DeviceLong,
		},
		Calibration: Calibration{
			GyroTCO: DefaultGyroTCO(),
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

// PodBurstWindowS returns the pod's burst-mode collect window (seconds); the
// Pi uses it to size the "quiet between bursts is healthy" link allowance.
func (c *Config) PodBurstWindowS() int {
	if c == nil || c.Pod.BurstWindowS == 0 {
		return DefaultPodBurstWindowS
	}
	return int(c.Pod.BurstWindowS)
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
	MergeGDL90Defaults(&c.GDL90)
	MergeGyroTCODefaults(&c.Calibration.GyroTCO)
	MigrateIMUOffuserFlags(c.Calibration.CabinIMU)
	return c, nil
}

// MigrateIMUOffuserFlags promotes legacy OffuserApplied into the per-sensor flags.
func MigrateIMUOffuserFlags(r *IMUCalResult) {
	if r == nil {
		return
	}
	if r.OffuserApplied && !r.AccelOffuserApplied && !r.GyroOffuserApplied {
		r.AccelOffuserApplied = true
		r.GyroOffuserApplied = true
	}
	r.OffuserApplied = r.AccelOffuserApplied || r.GyroOffuserApplied
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
	h.SetNotify(c, true)
}

// SetNoNotify replaces the live config without signaling reload subscribers.
// Used when a critical section (e.g. OFFUSER programming) writes sysfs itself
// and concurrent applyConfiguredAttrs would race on the I²C bus.
func (h *Holder) SetNoNotify(c *Config) {
	h.SetNotify(c, false)
}

// SetNotify replaces the live config and optionally signals reload subscribers.
func (h *Holder) SetNotify(c *Config, notify bool) {
	h.mu.Lock()
	subs := h.reloads
	h.cfg = c
	h.mu.Unlock()
	if !notify {
		return
	}
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
