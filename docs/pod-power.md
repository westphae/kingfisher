# Pod power protocol

The wing pod's power policy exists to serve one goal: **the stored data is the
product**. No stage ever discards sensor data to save power — low battery
defers delivery; only imminent LiPo damage stops collection.

## Why (assessment, July 2026)

Measured board draw (bq27441, 2026-07-11 flight): **~100 mA** ≈ radio
RX-always-on ~65-70 mA + CPU ~20-25 mA + sensors ~5 mA. 2000 mAh ≈ 20 h.
The retired Phase-4 "sleep" quiesced sensors (losing all data) while leaving
radio + CPU powered — ~10-20% saving for 100% data loss, and its 20% SOC
threshold was sized for the old 750 mAh pack.

Burst-cycle math: reconnect costs ~3 s at ~110 mA per cycle. At 60 s windows
that is +5 mA average on a ~30 mA radio-off floor; longer windows change
little (120 s saves only 2.5 mA more), so window length is a RAM choice, not a
power choice.

| Mode | Draw | 2000 mAh | Last 20% |
|---|---|---|---|
| Live streaming (today) | ~100 mA | ~20 h | ~4 h |
| Burst (radio off between syncs) | ~31-37 mA | ~55-65 h | ~11-13 h |
| Deep sleep (protect) | ~5 µA | — | — |

## The three stages (`firmware/pod/src/power.rs`)

1. **Active** — live streaming, exactly as before.
2. **Burst** — on battery with SOC ≤ `burst_soc_pct` (default 30%; voltage
   fallback `burst_voltage_v_uncalibrated` 3.60 V when the gauge is not
   trusted), debounced `low_debounce_s` (45 s): the radio is stopped
   (`esp_wifi_stop` via `radio.rs` — esp-radio 0.18 has no public stop/start;
   its event-driven state machine stays coherent with the raw calls). Sensors
   keep sampling at full rate into `burst.rs` — compact per-sensor rings
   (~149 KB .bss) sized for 50 Hz mag/static over one `burst_window_s`
   (default 60 s). Each window (or early at 90% ring fill) the radio comes up,
   the backlog drains as normal wire batches, and the radio goes back down.
   Ring overflow overwrites oldest and is counted in `dropped_readings`.
   Hysteresis +5% SOC / +0.05 V to return to Active; charging returns
   instantly.
3. **Protect** — voltage ≤ `protect_voltage_v` (3.50) or SOC ≤
   `protect_soc_pct` (5%), debounced; immediate below 3.40 V: final drain +
   Status, then true deep sleep (~5 µA) waking every 10 min for a minimal
   pre-WiFi gauge check (`bq27441::quick_check` in main.rs). Resumes on
   charging or recovery; otherwise sleeps again. This is the LiPo protection —
   and the only stage that stops collecting.

## Wire / Pi contract

No wire-format change. `Status.power_mode` values: 0 active, 1/2 legacy
quiesce-sleep (retired), 3 burst-collect, 4 burst-uplink, 5 protect-pending,
6 protect. Drained batches carry fresh `pod_uptime_us` with per-reading
`age_us`, so the Pi's EMA clock offset stays valid across bursts
(`tsClampPast` is 10 min to admit the backlog).

Pi side (`internal/pod`): burst/protect modes are not staleness-gated in
`LinkStats`; `burst_quiet` marks silence within one window + 90 s as healthy
(chip shows a neutral "burst" state, warn when overdue; "protect" warns).
The pod's `dropped_readings` counter is **no longer re-baselined after quiet
gaps** — since burst mode, anything the pod drops is stored-data loss and must
warn; only a counter reset (pod reboot) re-baselines.

## Modem power-save: verified working — ON in the aircraft config

esp-radio 0.18 has `Controller::set_power_saving(PowerSaveMode::Minimum)`
(unstable feature), set once on first association when
`pod.modem_power_save` is true. An initial bench run looked like the Pi's
brcmfmac AP dropped unicast to the dozing STA (re-Hello every 30 s, SetRate
never applied) — that was actually a pre-existing firmware bug:
`cmd::handle_datagram` did not `touch_inbound` for Ping frames, so the 5 s
keepalive never suppressed the 30 s Hello rediscovery. With that fixed,
power-save ran clean on the real AP (2 min, zero re-Hellos, cmds acked).
The build default stays false (conservative for fresh setups); the aircraft
config sets it true. Expected: ~100 → ~55-65 mA while staying fully live.

## Config (`~/.config/kingfisher/config.json`, `pod` section)

`burst_soc_pct` 30 · `burst_window_s` 60 · `burst_voltage_v_uncalibrated`
3.60 · `protect_voltage_v` 3.50 · `protect_soc_pct` 5 · `low_debounce_s` 45 ·
`modem_power_save` false. All build-time (`build.rs` → `cfg.rs`); the Pi reads
`burst_window_s` for the link-quiet allowance. Replaced: `sleep_soc_pct`,
`sleep_voltage_v_uncalibrated`, `sleep_debounce_s`, `sleep_emergency_voltage_v`.

## Deliberate v1 simplifications

- 60 s f32 rings instead of 5 min compressed: ~3 mA from optimal, no
  quantization layer. Revisit with delta/quantized packing if longer windows
  are wanted.
- Rings assume ≤50 Hz; higher rates shorten the effective window via the 90%
  early-uplink trigger rather than growing buffers.
- No flash-backed store: an AP outage longer than the ring keeps only the
  newest window (counted as drops).

## Bench hazard: charging the pod from Pi USB

Plugging the pod into the Pi's USB with a deeply discharged battery attached
makes the BQ25185 draw its full input current the instant VBUS appears
(charge current + ESP32 load, plus inrush). Under the Pi 5's default 600 mA
downstream budget this trips the RP1's *shared* VBUS over-current switch in a
continuous storm: **every** USB device drops, including a wifi dongle, so the
Pi can look dead while it is running fine (observed 2026-07-15; the 5 V rail
never sagged). The Pi now sets `usb_max_current_enable=1` (1.6 A budget,
5 V/5 A supply required). Even so: detach or pre-charge the battery before
connecting the pod to the Pi, or strap the BQ25185 ILIM ≤ 500 mA.
