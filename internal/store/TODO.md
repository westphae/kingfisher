# Flight DB: metadata table

`metadata` is a string key/value table written throughout the session, not a
placeholder. Callers today:

- Startup clock probe and chrony/PPS snapshot (`cmd/kingfisher`, keys
  `clock_startup_*`). See `docs/timestamps.md`.
- First chrony lock (`clock_synced_at_utc`, `clock_true_start_utc`).
- In-flight clock nudges (`clock_nudge_*` in `internal/clock/nudger.go`).
- Cockpit power-off (`shutdown_reason`, `shutdown_at_utc`).
- UPS gauge version and host-health snapshots when those monitors run.

`_session` is still the one-row flight header (tail, notes, declination seeded
to 0). `sensor_attrs` is the per-device configuration history.

## Not in metadata yet

- Pod firmware version / wire revision echoed from `Hello`.
- Hash of the effective `config.json` at flight start.
- Final geomagnetic model epoch (declination itself is the `geo` table).
