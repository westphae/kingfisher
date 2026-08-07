package sensors

import (
	"fmt"
	"log"
)

// programCalibbiasAttrs writes every calibbias key in attrs (full IIO names
// like in_anglvel_x_calibbias). Unchanged values are skipped. On mid-batch
// failure, prior values written in this call are restored.
//
// Cabin ICM-45686 OFFUSER is safe to program while IIO buffers are live
// (driver quiets the chip FIFO and shadows values across ODR/FS changes).
func programCalibbiasAttrs(r Reader, device string, attrs map[string]string) error {
	type item struct {
		key, ch, want, prev string
	}
	var items []item
	for k, v := range attrs {
		ch, attr := SplitIIOAttr(k)
		if attr != "calibbias" {
			continue
		}
		prev, err := r.ChannelAttr(ch, "calibbias")
		if err != nil {
			return fmt.Errorf("sensors: read %s %s before write: %w", device, k, err)
		}
		if prev == v {
			continue
		}
		items = append(items, item{key: k, ch: ch, want: v, prev: prev})
	}
	if len(items) == 0 {
		return nil
	}
	written := 0
	for i, it := range items {
		if err := r.SetChannelAttr(it.ch, "calibbias", it.want); err != nil {
			for j := 0; j < written; j++ {
				rb := items[j]
				if rerr := r.SetChannelAttr(rb.ch, "calibbias", rb.prev); rerr != nil {
					log.Printf("sensors: %s rollback %s: %v", device, rb.key, rerr)
				}
			}
			return fmt.Errorf("sensors: %s calibbias aborted at %s: %w", device, it.key, err)
		}
		written = i + 1
	}
	return nil
}

func extractCalibbias(attrs map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range attrs {
		if _, attr := SplitIIOAttr(k); attr == "calibbias" {
			out[k] = v
		}
	}
	return out
}

func applyNonCalibbiasAttrs(r Reader, devAttrs map[string]string) error {
	if len(devAttrs) == 0 {
		return nil
	}
	var firstErr error
	set := func(k, v string) {
		ch, attr := SplitIIOAttr(k)
		if err := r.SetChannelAttr(ch, attr, v); err != nil && firstErr == nil {
			firstErr = fmt.Errorf("setattr %s=%s: %w", k, v, err)
		}
	}
	if v, ok := devAttrs["sampling_frequency"]; ok {
		set("sampling_frequency", v)
	}
	for k, v := range devAttrs {
		if k == "sampling_frequency" {
			continue
		}
		if _, attr := SplitIIOAttr(k); attr == "calibbias" {
			continue
		}
		set(k, v)
	}
	return firstErr
}
