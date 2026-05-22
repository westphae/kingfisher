package sensors

import (
	"strconv"
	"strings"

	"github.com/westphae/kingfisher/internal/store"
)

// settingsChannelAttrs are the channel-level attribute suffixes that
// represent persistent configuration (as opposed to per-sample data like
// raw/input). The probe simply tries each and ignores ENOENT-equivalents.
var settingsChannelAttrs = []string{
	"scale",
	"offset",
	"calibbias",
	"calibscale",
	"filter_low_pass_3db_frequency",
	"sampling_frequency",
	"oversampling_ratio",
}

// settingsDeviceAttrs are device-level attribute names worth recording.
var settingsDeviceAttrs = []string{
	"sampling_frequency",
	"current_timestamp_clock",
}

// SnapshotAttrs probes the well-known IIO settings attributes for every
// channel of the reader plus a small set of device-level attrs. Missing
// attrs (ENOENT) are skipped silently. The returned records are stable in
// order so a diff against a previous snapshot is straightforward.
func SnapshotAttrs(r Reader) []store.AttrRecord {
	var out []store.AttrRecord
	chs := r.Channels()
	for _, ch := range chs {
		// "scale" and "offset" on per-axis channels are usually inherited
		// from the type-level parent (e.g. "accel"); read both so the DB
		// has the value the kernel actually applies.
		for _, attr := range settingsChannelAttrs {
			if v, err := r.ChannelAttr(ch, attr); err == nil {
				out = append(out, store.AttrRecord{
					Channel: ch,
					Attr:    attr,
					Value:   strings.TrimSpace(v),
				})
			}
		}
	}
	for _, attr := range settingsDeviceAttrs {
		if v, err := r.Attr(attr); err == nil {
			out = append(out, store.AttrRecord{
				Channel: "",
				Attr:    attr,
				Value:   strings.TrimSpace(v),
			})
		}
	}
	return out
}

// AttrOptions returns the parsed contents of the `_available` sysfs file
// for the given attribute, or nil if no such file exists. IIO conventionally
// publishes valid values as a whitespace-separated list, but some drivers
// use brackets or a "[low high]" continuous-range form; we strip brackets
// and return individual tokens.
func AttrOptions(r Reader, channel, attr string) []string {
	var (
		raw string
		err error
	)
	if channel == "" {
		raw, err = r.Attr(attr + "_available")
	} else {
		raw, err = r.ChannelAttr(channel, attr+"_available")
	}
	if err != nil {
		return nil
	}
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	// Range form "[low step high]" — common on pod caps and some IIO drivers.
	if strings.HasPrefix(raw, "[") {
		return optionsFromBracketRange(raw)
	}
	fields := strings.Fields(raw)
	out := make([]string, 0, len(fields))
	for _, f := range fields {
		f = strings.Trim(f, "[](),")
		if f != "" {
			out = append(out, f)
		}
	}
	return out
}

// hzRatePresets are offered for integer Hz ranges (pod caps, IIO sampling_frequency).
var hzRatePresets = []float64{1, 2, 5, 10, 15, 20, 25, 50, 100, 200}

func optionsFromHzPresets(min, max float64) []string {
	var out []string
	for _, v := range hzRatePresets {
		if v >= min-1e-9 && v <= max+1e-9 {
			out = append(out, strconv.FormatFloat(v, 'f', -1, 64))
		}
	}
	return out
}

// optionsFromBracketRange parses "[min step max]" (e.g. pod sampling_frequency_available)
// into a small dropdown list.
func optionsFromBracketRange(raw string) []string {
	inner := strings.Trim(raw, "[]")
	parts := strings.Fields(inner)
	if len(parts) != 3 {
		return nil
	}
	min, err1 := strconv.ParseFloat(parts[0], 64)
	step, err2 := strconv.ParseFloat(parts[1], 64)
	max, err3 := strconv.ParseFloat(parts[2], 64)
	if err1 != nil || err2 != nil || err3 != nil || step <= 0 || max < min {
		return nil
	}
	// Pod and most IIO drivers use step 1 Hz; avoid 1..N integer menus when max shrinks.
	if step == 1 {
		if out := optionsFromHzPresets(min, max); len(out) > 0 {
			return out
		}
	}
	span := max - min
	if span/step > 30 {
		if out := optionsFromHzPresets(min, max); len(out) > 0 {
			return out
		}
	}
	const limit = 24
	var out []string
	for v := min; v <= max+1e-9 && len(out) < limit; v += step {
		out = append(out, strconv.FormatFloat(v, 'f', -1, 64))
	}
	if len(out) > 0 {
		return out
	}
	return nil
}

// DiffAttrs returns the subset of `curr` whose values differ from `prev`
// (keyed by channel+attr). Records present in `prev` but missing from
// `curr` are not emitted — we treat disappearance as "stop polling that
// attr", which only happens on driver reload.
func DiffAttrs(prev, curr []store.AttrRecord) []store.AttrRecord {
	prevIdx := make(map[string]string, len(prev))
	for _, r := range prev {
		prevIdx[r.Channel+"\x00"+r.Attr] = r.Value
	}
	var out []store.AttrRecord
	for _, r := range curr {
		if p, ok := prevIdx[r.Channel+"\x00"+r.Attr]; !ok || p != r.Value {
			out = append(out, r)
		}
	}
	return out
}
