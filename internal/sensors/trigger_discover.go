package sensors

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
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

// syncDeviceSamplingHz aligns the chip ODR with kingfisher's buffered publish
// rate so FIFO watermark IRQs fire near the requested sample_hz.
func syncDeviceSamplingHz(r *iioReader, hz float64) error {
	if hz <= 0 || !usesHWFIFOBuffer(r.Name()) {
		return nil
	}
	if !r.WritableAttr("", "sampling_frequency") {
		return nil
	}
	return r.SetChannelAttr("", "sampling_frequency", fmt.Sprintf("%.0f", hz))
}
