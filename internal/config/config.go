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

type PressAlt struct {
	KollsmanInHg float64 `json:"kollsman_inhg"`
}

type AHRS struct {
	Enabled bool    `json:"enabled"`
	RateHz  float64 `json:"rate_hz"`
}

const DefaultKollsmanInHg = 29.92

// Pod holds wing-pod WiFi/UDP settings and persisted sensor attrs in
// config.json. Firmware build.rs reads wifi_ssid/password/udp_addr.
// Attrs are written on each UI change (config.Save) and survive power-off.
type Pod struct {
	WiFiSSID     string            `json:"wifi_ssid,omitempty"`
	WiFiPassword string            `json:"wifi_password,omitempty"`
	UDPAddr      string            `json:"udp_addr,omitempty"` // Pi host:port pod sends to; kingfisher binds :port on all ifaces
	Attrs        map[string]string `json:"attrs,omitempty"`    // e.g. in_mag_sampling_frequency
}

const defaultPodUDPAddr = "192.168.10.1:47808"

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
	PressAlt   PressAlt          `json:"press_alt"`
	AHRS       AHRS              `json:"ahrs"`
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
			WiFiSSID:     "kingfisher",
			WiFiPassword: "",
		},
		GPSFields: []string{
			"lat", "lon", "alt_msl", "gs", "track", "vs",
			"h_acc", "v_acc", "gs_acc", "vs_acc", "track_acc",
			"fix", "sats",
		},
		Devices:  map[string]Device{},
		GPS:      GPS{RateHz: 5},
		PressAlt: PressAlt{KollsmanInHg: DefaultKollsmanInHg},
		AHRS:     AHRS{Enabled: true, RateHz: 20},
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
