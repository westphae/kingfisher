# Flight DB: metadata table

The `metadata` table is created at DB bootstrap but **nothing writes to it yet**
(`SetMeta` exists in `store.go` but has no callers). `_session` holds the
startup snapshot; `sensor_attrs` holds per-device configuration history.

## Future uses for `metadata`

- Final geomagnetic declination after GPS lock (today only `_session.declination`
  is seeded to 0 at startup).
- Pod firmware version / wire revision echoed from `Hello` (complement `_session.version`).
- Effective `config.json` hash or path at flight start for reproducibility.
- User flight notes mirrored from config if we want them queryable without parsing config.
- AHRS tuning or calibration blobs that do not fit `sensor_attrs` rows.

Handle in a later pass; do not block pod attr logging on this.
