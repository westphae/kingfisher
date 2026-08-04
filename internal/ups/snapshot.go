package ups

// Snapshot is the /api/status view of the UPS, JSON-shaped for the header
// chip and status drawer (pod.LinkStats pattern).
//
// There are no shutdown-policy fields here: the x120x kernel driver and
// UPower own that decision, not kingfisher. PoweroffSocPct is reported only
// so the UI can say what SOC the machine will power off at, and so
// TimeRemainingS means time-until-poweroff.
type Snapshot struct {
	Enabled bool `json:"enabled"`
	Present bool `json:"present"` // gauge open and responding
	PLDOk   bool `json:"pld_ok"`  // AC state readable from the driver

	VoltageV       float64 `json:"voltage_v"`
	SocPct         float64 `json:"soc_pct"`
	ACOk           bool    `json:"ac_ok"`
	OnBatteryS     float64 `json:"on_battery_s"`
	TimeRemainingS float64 `json:"time_remaining_s"` // <0 = not yet estimable

	PoweroffSocPct float64 `json:"poweroff_soc_pct"` // informational: UPower's action threshold

	LastError  string  `json:"last_error,omitempty"`
	SampleAgeS float64 `json:"sample_age_s"`
}
