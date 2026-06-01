//! Battery-aware sleep policy and runtime power state.

use core::cell::RefCell;
use core::sync::atomic::{AtomicBool, AtomicU8, Ordering};

use critical_section::Mutex;
use esp_println::println;

use crate::cfg;

/// Positive average current (A) at or above this is treated as charging.
const CHARGE_CURRENT_A: f32 = 0.05;

/// Voltage hysteresis (V) above uncalibrated sleep threshold to wake.
const WAKE_VOLTAGE_MARGIN_V: f32 = 0.05;

/// SOC hysteresis (%) above sleep threshold to wake when gauge is trusted.
const WAKE_SOC_MARGIN_PCT: f32 = 5.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PowerMode {
    Active = 0,
    SleepPending = 1,
    Sleeping = 2,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SleepReason {
    None = 0,
    Soc = 1,
    VoltageFallback = 2,
    Emergency = 3,
}

static MODE: AtomicU8 = AtomicU8::new(PowerMode::Active as u8);
static REASON: AtomicU8 = AtomicU8::new(SleepReason::None as u8);
static REQUESTED: AtomicBool = AtomicBool::new(false);
static LOW_SINCE_US: Mutex<RefCell<u64>> = Mutex::new(RefCell::new(0));

fn store_reason(reason: SleepReason) {
    REASON.store(reason as u8, Ordering::Relaxed);
}

pub fn mode() -> PowerMode {
    match MODE.load(Ordering::Relaxed) {
        1 => PowerMode::SleepPending,
        2 => PowerMode::Sleeping,
        _ => PowerMode::Active,
    }
}

pub fn sleep_reason() -> SleepReason {
    match REASON.load(Ordering::Relaxed) {
        1 => SleepReason::Soc,
        2 => SleepReason::VoltageFallback,
        3 => SleepReason::Emergency,
        _ => SleepReason::None,
    }
}

pub fn sleep_requested() -> bool {
    REQUESTED.load(Ordering::Relaxed)
}

pub fn mark_sleeping() {
    MODE.store(PowerMode::Sleeping as u8, Ordering::Relaxed);
}

fn is_charging(current_a: f32) -> bool {
    current_a >= CHARGE_CURRENT_A
}

fn clear_sleep() {
    REQUESTED.store(false, Ordering::Relaxed);
    MODE.store(PowerMode::Active as u8, Ordering::Relaxed);
    store_reason(SleepReason::None);
    critical_section::with(|cs| *LOW_SINCE_US.borrow(cs).borrow_mut() = 0);
}

/// Returns true when the pack looks healthy enough to leave sleep.
fn should_wake(voltage_v: f32, soc_pct: f32, gauge_trusted: bool) -> bool {
    if gauge_trusted {
        return soc_pct > cfg::SLEEP_SOC_PCT as f32 + WAKE_SOC_MARGIN_PCT;
    }
    voltage_v > cfg::SLEEP_VOLTAGE_UNCALIBRATED + WAKE_VOLTAGE_MARGIN_V
}

/// Update policy from a fuel-gauge sample. Returns true when sleep is requested.
pub fn note_battery_sample(
    now_us: u64,
    voltage_v: f32,
    current_a: f32,
    soc_pct: f32,
    gauge_trusted: bool,
) -> bool {
    if is_charging(current_a) {
        if REQUESTED.load(Ordering::Relaxed) {
            println!("pod: wake from sleep (charging {:.0} mA)", current_a * 1000.0);
            clear_sleep();
        }
        return false;
    }

    if REQUESTED.load(Ordering::Relaxed) {
        if should_wake(voltage_v, soc_pct, gauge_trusted) {
            println!("pod: wake from sleep (recovered)");
            clear_sleep();
        }
        return REQUESTED.load(Ordering::Relaxed);
    }

    if voltage_v > 0.0 && voltage_v <= cfg::SLEEP_EMERGENCY_VOLTAGE {
        REQUESTED.store(true, Ordering::Relaxed);
        MODE.store(PowerMode::SleepPending as u8, Ordering::Relaxed);
        store_reason(SleepReason::Emergency);
        return true;
    }

    let low = if gauge_trusted {
        soc_pct <= cfg::SLEEP_SOC_PCT as f32
    } else {
        voltage_v > 0.0 && voltage_v <= cfg::SLEEP_VOLTAGE_UNCALIBRATED
    };
    if !low {
        critical_section::with(|cs| *LOW_SINCE_US.borrow(cs).borrow_mut() = 0);
        return false;
    }

    let since = critical_section::with(|cs| *LOW_SINCE_US.borrow(cs).borrow());
    if since == 0 {
        critical_section::with(|cs| *LOW_SINCE_US.borrow(cs).borrow_mut() = now_us);
        return false;
    }
    if now_us.saturating_sub(since) < cfg::SLEEP_DEBOUNCE_US {
        return false;
    }
    REQUESTED.store(true, Ordering::Relaxed);
    MODE.store(PowerMode::SleepPending as u8, Ordering::Relaxed);
    if gauge_trusted {
        store_reason(SleepReason::Soc);
    } else {
        store_reason(SleepReason::VoltageFallback);
    }
    true
}
