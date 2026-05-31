// Package clock reports host wall-clock discipline from chrony and related probes.
package clock

import (
	"context"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	SourcePPS     = "pps"
	SourceGPS     = "gps"
	SourceNTP     = "ntp"
	SourceLocal   = "local"
	SourceUnknown = "unknown"
)

// DisciplineStatus is chrony's view of how the Pi wall clock is steered.
type DisciplineStatus struct {
	Available   bool
	Synced      bool
	Source      string
	SourceLabel string
	Stratum     int
	LastOffset  time.Duration
	RMSOffset   time.Duration
	PPSPresent  bool
	PPSSteering bool
}

var (
	reRefID     = regexp.MustCompile(`(?m)^Reference ID\s+:\s+\S+\s+\(([^)]*)\)\s*$`)
	reStratum   = regexp.MustCompile(`(?m)^Stratum\s+:\s+(\d+)\s*$`)
	reLastOff   = regexp.MustCompile(`(?m)^Last offset\s+:\s+([+-]?[\d.]+)\s+seconds\s*$`)
	reRMSOff    = regexp.MustCompile(`(?m)^RMS offset\s+:\s+([\d.]+)\s+seconds\s*$`)
	reActiveSrc = regexp.MustCompile(`(?m)^#\*\s+(\S+)`)
)

// PPSPresent reports whether the kernel PPS device node exists.
func PPSPresent() bool {
	_, err := os.Stat("/dev/pps0")
	return err == nil
}

// QueryDiscipline runs chronyc and parses tracking plus the selected source.
func QueryDiscipline(ctx context.Context) DisciplineStatus {
	st := DisciplineStatus{PPSPresent: PPSPresent()}
	if _, err := exec.LookPath("chronyc"); err != nil {
		return st
	}

	qctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()

	out, err := exec.CommandContext(qctx, "chronyc", "tracking").Output()
	if err != nil {
		return st
	}
	st.Available = true
	parseTracking(string(out), &st)

	if srcOut, err := exec.CommandContext(qctx, "chronyc", "sources").Output(); err == nil {
		parseActiveSource(string(srcOut), &st)
	}
	return st
}

func parseTracking(text string, st *DisciplineStatus) {
	if m := reRefID.FindStringSubmatch(text); len(m) == 2 {
		st.SourceLabel = strings.TrimSpace(m[1])
		st.Source = classifySourceLabel(st.SourceLabel)
	}
	if m := reStratum.FindStringSubmatch(text); len(m) == 2 {
		st.Stratum, _ = strconv.Atoi(m[1])
	}
	if m := reLastOff.FindStringSubmatch(text); len(m) == 2 {
		st.LastOffset = parseSeconds(m[1])
	}
	if m := reRMSOff.FindStringSubmatch(text); len(m) == 2 {
		st.RMSOffset = parseSeconds(m[1])
	}
	st.Synced = st.Stratum >= 1 && st.Stratum <= 15 && st.Source != SourceLocal && st.Source != SourceUnknown
}

func parseActiveSource(text string, st *DisciplineStatus) {
	m := reActiveSrc.FindStringSubmatch(text)
	if len(m) != 2 {
		return
	}
	name := strings.ToUpper(strings.TrimSpace(m[1]))
	st.PPSSteering = name == "PPS"
	if name != "" {
		st.SourceLabel = m[1]
		st.Source = classifySourceLabel(m[1])
	}
}

func classifySourceLabel(label string) string {
	u := strings.ToUpper(strings.TrimSpace(label))
	switch {
	case u == "PPS":
		return SourcePPS
	case u == "GPS":
		return SourceGPS
	case u == "":
		return SourceLocal
	case strings.Contains(u, ".") || strings.Contains(u, "POOL") || strings.HasPrefix(u, "NTP"):
		return SourceNTP
	default:
		if len(u) == 4 && isHexRef(u) {
			return SourceUnknown
		}
		return SourceNTP
	}
}

func isHexRef(s string) bool {
	for _, c := range s {
		if (c < '0' || c > '9') && (c < 'A' || c > 'F') && (c < 'a' || c > 'f') {
			return false
		}
	}
	return true
}

func parseSeconds(s string) time.Duration {
	f, err := strconv.ParseFloat(strings.TrimSpace(s), 64)
	if err != nil {
		return 0
	}
	return time.Duration(f * float64(time.Second))
}

// StartupMeta returns metadata key/value pairs to persist when a flight DB is
// opened. Keys use the clock_startup_* prefix alongside the GPS probe keys in
// cmd/kingfisher/main.go.
func StartupMeta(disc DisciplineStatus) map[string]string {
	m := map[string]string{
		"clock_startup_pps_present": strconv.FormatBool(disc.PPSPresent),
	}
	if !disc.Available {
		m["clock_startup_chrony_available"] = "false"
		return m
	}
	m["clock_startup_chrony_available"] = "true"
	m["clock_startup_chrony_synced"] = strconv.FormatBool(disc.Synced)
	m["clock_startup_chrony_source"] = disc.Source
	m["clock_startup_pps_steering"] = strconv.FormatBool(disc.PPSSteering)
	if disc.SourceLabel != "" {
		m["clock_startup_chrony_source_label"] = disc.SourceLabel
	}
	if disc.Stratum > 0 {
		m["clock_startup_chrony_stratum"] = strconv.Itoa(disc.Stratum)
	}
	return m
}
