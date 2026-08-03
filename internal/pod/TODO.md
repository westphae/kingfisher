# Pod ↔ Pi: attrs in flight DB and wire

## Done (Pi)

- `sensor_attrs` rows for wing chip devices (`bmp581`, etc.) with `location=pod` on Hello,
  config reload (diff), and successful `SetRate` Ack.
- Rows include `sampling_frequency` plus Hello cap metadata (`min_hz`, `max_hz`,
  `default_hz`) when caps are known.

## Later: Hello carries applied chip attrs

Today `Hello` only advertises `SensorCap` (min/max/default Hz). The firmware
already applies BMP581/MMC5983/MS4525 driver settings at boot, but the Pi infers
rates from `config.json` + caps, not from an explicit attr snapshot on the wire.

**Plan:**

1. Extend `pod_wire::Hello` (or nested cap struct) with optional applied attrs
   per sensor (ODR, oversampling, filter, etc.) — postcard-stable field adds.
2. Populate in `firmware/pod/src/hello.rs` from actual driver state after init.
3. Pi: on `applyHello`, log those rows to `sensor_attrs` (source of truth =
   pod, not Pi cache).

## Done: BMP581 OSR / IIR via SetAttr

`AttrKey::{BmpOsrPress,BmpOsrTemp,BmpIirPress,BmpIirTemp}` + firmware
`bmp_cfg` / `bmp581` apply path. Config keys:

- `in_bmp581_sampling_frequency` (cruise default **25**)
- `in_bmp581_oversampling_pressure` / `_temp` (multipliers; default **32** / **2**)
- `in_bmp581_iir_pressure` / `_temp` (coeffs 0=bypass,1,3,…; default **3**)

## Later: more configurable chip registers

- MMC5983 / MS4525 attrs via the same `SetAttr` pattern.
- Ack + rollback for BMP attrs (today optimistic cache like DesignCapacity without chip confirm).
- Log attr changes to `sensor_attrs` on successful Ack.
