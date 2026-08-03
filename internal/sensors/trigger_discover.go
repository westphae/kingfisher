package sensors

import (
	"fmt"
	"log"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/westphae/kingfisher/internal/config"
)

// discoverIIOTrigger returns the kernel trigger name for a chip-backed data-ready
// trigger (e.g. inv_icm45600 INT1) when one is registered under
// /sys/bus/iio/devices/triggerN/name. The kernel name is suitable for writing to
// trigger/current_trigger. Returns "" when none match.
func discoverIIOTrigger(kernelName string) string {
	base := strings.ToLower(strings.TrimSpace(kernelName))
	if base == "" {
		return ""
	}
	// icm45686-gyro → icm45686
	if i := strings.LastIndex(base, "-"); i > 0 {
		base = base[:i]
	}

	matches, err := filepath.Glob("/sys/bus/iio/devices/trigger*")
	if err != nil {
		return ""
	}
	for _, dir := range matches {
		b, err := os.ReadFile(filepath.Join(dir, "name"))
		if err != nil {
			continue
		}
		n := strings.TrimSpace(string(b))
		if n == "" {
			continue
		}
		low := strings.ToLower(n)
		if strings.Contains(low, base) || strings.HasPrefix(low, base) {
			return n
		}
	}
	return ""
}

// usesHWFIFOBuffer reports devices whose driver fills the IIO buffer from an
// on-chip FIFO via INT1 (no userspace trigger/current_trigger). Buffered reads
// must not rely on buffer/data_available before read(2).
func usesHWFIFOBuffer(kernelName string) bool {
	low := strings.ToLower(kernelName)
	return strings.HasPrefix(low, "icm45686")
}

// snapSamplingFrequency picks the nearest rate from sampling_frequency_available
// and returns the exact sysfs token so the driver accepts the write.
func snapSamplingFrequency(hz float64, available string) (token string, snappedHz float64, err error) {
	if hz <= 0 {
		return "", 0, fmt.Errorf("sensors: snap sampling_frequency: hz must be > 0")
	}
	bestDiff := math.MaxFloat64
	for _, tok := range strings.Fields(available) {
		v, perr := strconv.ParseFloat(tok, 64)
		if perr != nil || v <= 0 {
			continue
		}
		diff := math.Abs(v - hz)
		if diff < bestDiff {
			bestDiff = diff
			token = tok
			snappedHz = v
		}
	}
	if token == "" {
		return "", 0, fmt.Errorf("sensors: snap sampling_frequency: no rates in %q", available)
	}
	return token, snappedHz, nil
}

// configuredChipHz returns the desired on-chip ODR for a buffered device.
// When attrs.sampling_frequency is set (and > 0) it wins — allowing chip ODR
// above sample_hz so the driver can LPF/average before kingfisher boxcars down
// to the publish rate. Otherwise the publish rate is used.
func configuredChipHz(dev config.Device, publishHz float64) float64 {
	if s, ok := dev.Attrs["sampling_frequency"]; ok {
		if v, err := strconv.ParseFloat(strings.TrimSpace(s), 64); err == nil && v > 0 {
			return v
		}
	}
	if publishHz > 0 {
		return publishHz
	}
	return 0
}

// boxcarRatio is how many chip frames to average into one published sample.
// Returns 1 when chip ODR is not meaningfully above the publish rate.
func boxcarRatio(chipHz, publishHz float64) int {
	if publishHz <= 0 || chipHz <= publishHz*1.05 {
		return 1
	}
	n := int(math.Round(chipHz / publishHz))
	if n < 2 {
		return 1
	}
	return n
}

// syncDeviceSamplingHz programs the chip ODR (sampling_frequency). The inv
// driver only accepts discrete values from sampling_frequency_available.
func syncDeviceSamplingHz(r *iioReader, hz float64) error {
	if hz <= 0 || !usesHWFIFOBuffer(r.Name()) {
		return nil
	}
	if !r.WritableAttr("", "sampling_frequency") {
		return nil
	}
	avail, err := r.Attr("sampling_frequency_available")
	if err != nil {
		return fmt.Errorf("sampling_frequency_available: %w", err)
	}
	token, snapped, err := snapSamplingFrequency(hz, avail)
	if err != nil {
		return err
	}
	if math.Abs(snapped-hz) > 1e-9 {
		log.Printf("sensors: %s: requested chip ODR %.4g -> sampling_frequency %s",
			r.Name(), hz, token)
	}
	return r.SetChannelAttr("", "sampling_frequency", token)
}
