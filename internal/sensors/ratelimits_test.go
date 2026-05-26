package sensors

import (
	"os"
	"strconv"
	"testing"
)

type bmp280TestReader struct {
	press, temp int
}

func (r *bmp280TestReader) Name() string { return "bmp280" }
func (r *bmp280TestReader) Channels() []string {
	return []string{"pressure", "temp"}
}
func (r *bmp280TestReader) ReadFloat(string) (float64, error) { return 0, nil }
func (r *bmp280TestReader) ChannelAttr(ch, attr string) (string, error) {
	if attr != "oversampling_ratio" {
		return "", os.ErrNotExist
	}
	switch ch {
	case "pressure":
		return strconv.Itoa(r.press), nil
	case "temp":
		return strconv.Itoa(r.temp), nil
	}
	return "", os.ErrNotExist
}
func (r *bmp280TestReader) Attr(string) (string, error)            { return "", os.ErrNotExist }
func (r *bmp280TestReader) SetChannelAttr(string, string, string) error { return nil }
func (r *bmp280TestReader) ReloadScale() error                     { return nil }
func (r *bmp280TestReader) WritableAttr(string, string) bool       { return false }
func (r *bmp280TestReader) Close() error                           { return nil }

func TestBmp280MaxBufferedHz(t *testing.T) {
	r := &bmp280TestReader{press: 16, temp: 16}
	max := bmp280MaxBufferedHz(r)
	// 43.2 + 12.5 = 55.7 ms → ~1000/(55.7*1.05) ≈ 17.1 → floor to 17.0
	if max < 15 || max > 18 {
		t.Fatalf("OSR 16+16 max hz: got %v want ~17", max)
	}
	if clampBufferedHz(r, 20) != max {
		t.Fatalf("clamp 20: got %v want %v", clampBufferedHz(r, 20), max)
	}
	if clampBufferedHz(r, 5) != 5 {
		t.Fatalf("clamp 5 should pass through")
	}
}

type icm20948TestReader struct{}

func (r *icm20948TestReader) Name() string                            { return "icm20948" }
func (r *icm20948TestReader) Channels() []string                      { return nil }
func (r *icm20948TestReader) ReadFloat(string) (float64, error)       { return 0, nil }
func (r *icm20948TestReader) ChannelAttr(string, string) (string, error) {
	return "", os.ErrNotExist
}
func (r *icm20948TestReader) Attr(string) (string, error)             { return "", os.ErrNotExist }
func (r *icm20948TestReader) SetChannelAttr(string, string, string) error { return nil }
func (r *icm20948TestReader) ReloadScale() error                      { return nil }
func (r *icm20948TestReader) WritableAttr(string, string) bool        { return false }
func (r *icm20948TestReader) Close() error                            { return nil }

func TestIcm20948MaxBufferedHz(t *testing.T) {
	r := &icm20948TestReader{}
	max, ok := MaxBufferedHz(r)
	if !ok {
		t.Fatal("expected icm20948 limits")
	}
	// 100 Hz mag aux / 1.05 margin → 95 Hz
	if max != 95 {
		t.Fatalf("max hz: got %v want 95", max)
	}
	if clampBufferedHz(r, 200) != 95 {
		t.Fatalf("clamp 200: got %v want 95", clampBufferedHz(r, 200))
	}
	if clampBufferedHz(r, 50) != 50 {
		t.Fatalf("clamp 50 should pass through")
	}
}
