//! Three-stage battery power protocol.
//!
//! Stored data is the product; live data is secondary. No stage ever discards
//! sensor data to save power — Burst defers it, Protect flushes before sleep.
//!
//! - **Active**: live streaming, modem power-save Minimum (set in main.rs).
//! - **Burst** (on battery, SOC/voltage low): radio off; sensors keep sampling
//!   into `burst`; every `BURST_WINDOW_S` (or early when the store nears full)
//!   the radio comes up just long enough to drain the backlog.
//! - **Protect** (pack near LiPo damage): final drain, then deep sleep with a
//!   periodic wake to check for charging (main.rs).
//!
//! Mode values 1/2 belonged to the retired Phase-4 quiesce-sleep and are never
//! emitted; the Pi still labels them for old firmware.

use core::cell::RefCell;
use core::sync::atomic::{AtomicU8, Ordering};

use critical_section::Mutex;
use esp_println::println;

use crate::cfg;

/// Positive average current (A) at or above this is treated as charging.
const CHARGE_CURRENT_A: f32 = 0.05;

/// Immediate (no-debounce) Protect this far below the configured threshold.
const PROTECT_EMERGENCY_MARGIN_V: f32 = 0.10;

/// Hysteresis to leave Burst for Active.
const WAKE_VOLTAGE_MARGIN_V: f32 = 0.05;
const WAKE_SOC_MARGIN_PCT: f32 = 5.0;

/// Give up on a burst uplink (AP unreachable) and go back to collecting.
const UPLINK_TIMEOUT_US: u64 = 45_000_000;

/// Max time in ProtectPending waiting for the final drain before sleeping
/// anyway — LiPo protection outranks the last buffer.
const PROTECT_FLUSH_TIMEOUT_US: u64 = 60_000_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PowerMode {
    Active = 0,
    // 1 = legacy SleepPending, 2 = legacy Sleeping (retired, never emitted)
    BurstCollect = 3,
    BurstUplink = 4,
    ProtectPending = 5,
    Protect = 6,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PowerReason {
    None = 0,
    Soc = 1,
    VoltageFallback = 2,
    Emergency = 3,
}

static MODE: AtomicU8 = AtomicU8::new(PowerMode::Active as u8);
static REASON: AtomicU8 = AtomicU8::new(PowerReason::None as u8);
// riscv32imc has no 64-bit atomics; these share the critical section.
static PHASE_START_US: Mutex<RefCell<u64>> = Mutex::new(RefCell::new(0));
static BURST_LOW_SINCE_US: Mutex<RefCell<u64>> = Mutex::new(RefCell::new(0));
static PROTECT_LOW_SINCE_US: Mutex<RefCell<u64>> = Mutex::new(RefCell::new(0));

fn get64(cell: &Mutex<RefCell<u64>>) -> u64 {
    critical_section::with(|cs| *cell.borrow(cs).borrow())
}

fn set64(cell: &Mutex<RefCell<u64>>, v: u64) {
    critical_section::with(|cs| *cell.borrow(cs).borrow_mut() = v);
}

pub fn mode() -> PowerMode {
    match MODE.load(Ordering::Relaxed) {
        3 => PowerMode::BurstCollect,
        4 => PowerMode::BurstUplink,
        5 => PowerMode::ProtectPending,
        6 => PowerMode::Protect,
        _ => PowerMode::Active,
    }
}

pub fn reason() -> PowerReason {
    match REASON.load(Ordering::Relaxed) {
        1 => PowerReason::Soc,
        2 => PowerReason::VoltageFallback,
        3 => PowerReason::Emergency,
        _ => PowerReason::None,
    }
}

fn set_mode(m: PowerMode, now_us: u64) {
    MODE.store(m as u8, Ordering::Relaxed);
    set64(&PHASE_START_US, now_us);
}

fn set_reason(r: PowerReason) {
    REASON.store(r as u8, Ordering::Relaxed);
}

/// True when the WiFi radio should be powered and associated.
pub fn radio_wanted() -> bool {
    matches!(
        mode(),
        PowerMode::Active | PowerMode::BurstUplink | PowerMode::ProtectPending
    )
}

/// True when sensor readings should go to the burst store instead of the
/// live pending queues.
pub fn defer_readings() -> bool {
    matches!(mode(), PowerMode::BurstCollect | PowerMode::Protect)
}

/// True when the uplink should drain everything and report completion
/// (burst uplink window or the final pre-sleep flush).
pub fn drain_requested() -> bool {
    matches!(mode(), PowerMode::BurstUplink | PowerMode::ProtectPending)
}

/// Time-driven transitions. Call once per uplink tick.
pub fn tick(now_us: u64) {
    let elapsed = now_us.saturating_sub(get64(&PHASE_START_US));
    match mode() {
        PowerMode::BurstCollect => {
            if elapsed >= cfg::BURST_WINDOW_US || crate::burst::nearly_full() {
                println!("pod: burst uplink window (buffered={})", crate::burst::depth());
                set_mode(PowerMode::BurstUplink, now_us);
            }
        }
        PowerMode::BurstUplink => {
            if elapsed >= UPLINK_TIMEOUT_US {
                println!("pod: burst uplink timed out; back to collect");
                set_mode(PowerMode::BurstCollect, now_us);
            }
        }
        PowerMode::ProtectPending => {
            if elapsed >= PROTECT_FLUSH_TIMEOUT_US {
                println!("pod: protect flush timed out; sleeping anyway");
                set_mode(PowerMode::Protect, now_us);
            }
        }
        _ => {}
    }
}

/// Uplink drained everything and sent Status during a burst window.
pub fn note_uplink_complete(now_us: u64) {
    if mode() == PowerMode::BurstUplink {
        set_mode(PowerMode::BurstCollect, now_us);
    }
}

/// Final pre-sleep flush finished (or was as complete as the link allowed).
pub fn note_protect_flushed(now_us: u64) {
    if mode() == PowerMode::ProtectPending {
        set_mode(PowerMode::Protect, now_us);
    }
}

fn is_charging(current_a: f32) -> bool {
    current_a >= CHARGE_CURRENT_A
}

fn clear_low_since() {
    set64(&BURST_LOW_SINCE_US, 0);
    set64(&PROTECT_LOW_SINCE_US, 0);
}

/// Debounce helper: returns true once `low` has held for LOW_DEBOUNCE_US.
fn debounced(cell: &Mutex<RefCell<u64>>, low: bool, now_us: u64) -> bool {
    if !low {
        set64(cell, 0);
        return false;
    }
    let since = get64(cell);
    if since == 0 {
        set64(cell, now_us);
        return false;
    }
    now_us.saturating_sub(since) >= cfg::LOW_DEBOUNCE_US
}

/// Update policy from a fuel-gauge sample (~1 Hz, runs in every mode).
pub fn note_battery_sample(
    now_us: u64,
    voltage_v: f32,
    current_a: f32,
    soc_pct: f32,
    gauge_trusted: bool,
) {
    let m = mode();

    if is_charging(current_a) {
        if m != PowerMode::Active {
            println!(
                "pod: power -> active (charging {:.0} mA)",
                current_a * 1000.0
            );
            set_mode(PowerMode::Active, now_us);
            set_reason(PowerReason::None);
        }
        clear_low_since();
        return;
    }

    // Leave Protect when the pack is clearly healthy. After a rundown the
    // charger often tapers below CHARGE_CURRENT_A (or is unplugged) at ~4.2 V;
    // an unlearned gauge must not keep us in Protect on a bogus low SOC.
    if m == PowerMode::ProtectPending || m == PowerMode::Protect {
        let volt_ok = voltage_v > cfg::BURST_VOLTAGE_UNCALIBRATED + WAKE_VOLTAGE_MARGIN_V;
        let soc_ok =
            !gauge_trusted || soc_pct > cfg::PROTECT_SOC_PCT as f32 + WAKE_SOC_MARGIN_PCT;
        if volt_ok && soc_ok {
            println!(
                "pod: power -> active (pack recovered {:.2} V, soc {:.0}%)",
                voltage_v, soc_pct
            );
            set_mode(PowerMode::Active, now_us);
            set_reason(PowerReason::None);
            clear_low_since();
            return;
        }
    }

    // Protect: immediate below the emergency floor, debounced at the
    // configured threshold. Voltage always counts; SOC needs a trusted gauge.
    if m != PowerMode::ProtectPending && m != PowerMode::Protect {
        if voltage_v > 0.0 && voltage_v <= cfg::PROTECT_VOLTAGE - PROTECT_EMERGENCY_MARGIN_V {
            println!("pod: power -> protect (emergency {:.2} V)", voltage_v);
            set_mode(PowerMode::ProtectPending, now_us);
            set_reason(PowerReason::Emergency);
            return;
        }
        let volt_low = voltage_v > 0.0 && voltage_v <= cfg::PROTECT_VOLTAGE;
        let soc_low = gauge_trusted && soc_pct <= cfg::PROTECT_SOC_PCT as f32;
        if debounced(&PROTECT_LOW_SINCE_US, volt_low || soc_low, now_us) {
            println!(
                "pod: power -> protect ({:.2} V, soc {:.0}%)",
                voltage_v, soc_pct
            );
            set_mode(PowerMode::ProtectPending, now_us);
            set_reason(if volt_low {
                PowerReason::VoltageFallback
            } else {
                PowerReason::Soc
            });
            return;
        }
    }

    match m {
        PowerMode::Active => {
            let low = if gauge_trusted {
                soc_pct <= cfg::BURST_SOC_PCT as f32
            } else {
                voltage_v > 0.0 && voltage_v <= cfg::BURST_VOLTAGE_UNCALIBRATED
            };
            if debounced(&BURST_LOW_SINCE_US, low, now_us) {
                println!(
                    "pod: power -> burst ({:.2} V, soc {:.0}%, window {} s)",
                    voltage_v,
                    soc_pct,
                    cfg::BURST_WINDOW_S
                );
                set_mode(PowerMode::BurstCollect, now_us);
                set_reason(if gauge_trusted {
                    PowerReason::Soc
                } else {
                    PowerReason::VoltageFallback
                });
                clear_low_since();
            }
        }
        PowerMode::BurstCollect | PowerMode::BurstUplink => {
            let recovered = if gauge_trusted {
                soc_pct > cfg::BURST_SOC_PCT as f32 + WAKE_SOC_MARGIN_PCT
            } else {
                voltage_v > cfg::BURST_VOLTAGE_UNCALIBRATED + WAKE_VOLTAGE_MARGIN_V
            };
            if recovered {
                println!("pod: power -> active (recovered)");
                set_mode(PowerMode::Active, now_us);
                set_reason(PowerReason::None);
                clear_low_since();
            }
        }
        // ProtectPending/Protect only exit via the charging branch above
        // (or the deep-sleep wake check in main).
        _ => {}
    }
}
