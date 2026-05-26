package sensors

import (
	"testing"
	"time"

	"github.com/westphae/go-iio"
	"github.com/westphae/kingfisher/internal/config"
)

func TestDeviceWantBuffer(t *testing.T) {
	if (config.Device{}).WantBuffer(0) {
		t.Fatal("no scan channels")
	}
	falseVal := false
	if (config.Device{UseBuffer: &falseVal}).WantBuffer(3) {
		t.Fatal("explicit false")
	}
	if !(config.Device{}).WantBuffer(3) {
		t.Fatal("default on")
	}
}

func TestRecordToValues(t *testing.T) {
	rec := iio.Record{
		Time: time.Unix(100, 0),
		Values: map[string]float64{
			"pressure": 101.325,
			"temp":     22.5,
		},
	}
	colMap := map[string]string{"accel_x": "ax"}
	vals := recordToValues(rec, colMap)
	if vals["pressure_pa"] != 101325 {
		t.Errorf("pressure: got %v want Pa", vals["pressure_pa"])
	}
	if vals["temp_c"] != 22.5 {
		t.Errorf("temp: got %v", vals["temp_c"])
	}
}

func TestBufferLengthForHz(t *testing.T) {
	if bufferLengthForHz(10) < minBufferLength {
		t.Fatalf("short buffer at 10 Hz")
	}
	if bufferLengthForHz(200) > maxBufferLength {
		t.Fatalf("capped buffer at 200 Hz")
	}
}

func TestTriggerNameSanitize(t *testing.T) {
	if triggerName("icm20948_2") != "kingfisher-icm20948-2" {
		t.Fatal(triggerName("icm20948_2"))
	}
}
