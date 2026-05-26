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

## Later: configurable chip registers

Wire already has `Cmd::SetAttr` with `AttrKey::Oversampling` and `IirFilter`;
firmware `cmd.rs` does not handle them yet. End state:

- Pi UI / `config.json` `pod.attrs` keys beyond `*_sampling_frequency`.
- Firmware applies via BMP581/MMC5983/MS4525 driver APIs; Ack + rollback like `SetRate`.
- Log attr changes to `sensor_attrs` on successful Ack.
