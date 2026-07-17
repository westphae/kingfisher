package ups

// Snapshot is the /api/status view of the UPS, JSON-shaped for the header
// chip and status drawer (pod.LinkStats pattern).
type Snapshot struct {
	Enabled bool `json:"enabled"`
	Present bool `json:"present"` // gauge open and responding
	PLDOk   bool `json:"pld_ok"`  // power-loss line readable

	VoltageV       float64 `json:"voltage_v"`
	SocPct         float64 `json:"soc_pct"`
	ACOk           bool    `json:"ac_ok"`
	OnBatteryS     float64 `json:"on_battery_s"`
	TimeRemainingS float64 `json:"time_remaining_s"` // <0 = not yet estimable

	ShutdownAfterS   float64 `json:"shutdown_after_s"` // 0 = run to floor
	ShutdownSocPct   float64 `json:"shutdown_soc_pct"`
	ShutdownVoltageV float64 `json:"shutdown_voltage_v"`
	ShutdownReason   string  `json:"shutdown_reason,omitempty"` // set once triggered

	LastError  string  `json:"last_error,omitempty"`
	SampleAgeS float64 `json:"sample_age_s"`
}
