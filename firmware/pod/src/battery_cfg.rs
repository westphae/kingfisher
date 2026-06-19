//! Runtime LiPo design capacity (mAh) for the BQ27441 gauge.
//!
//! The gauge's own non-volatile data memory (DesignCapacity, register 0x3C) is
//! the source of truth. We do not keep an authoritative copy here: we only track
//! a *pending* value the Pi asked us to write and the *last readback* of 0x3C, and
//! drive programming by comparing the two. The build-time
//! [`crate::cfg::BATTERY_CAPACITY_MAH`] is used only to seed a blank gauge.

use core::cell::Cell;
use core::sync::atomic::{AtomicBool, AtomicU16, Ordering};

use critical_section::Mutex;

use crate::cfg;

/// Wait this long after a failed config update before retrying (µs).
const PROGRAM_FAIL_BACKOFF_US: u64 = 60_000_000;

/// Plausible design-capacity bounds for a SetAttr request (mAh).
const MIN_DESIGN_MAH: u16 = 100;
const MAX_DESIGN_MAH: u16 = 32_000;

/// Value the Pi most recently asked us to program (0 = nothing pending).
static PENDING_TARGET: AtomicU16 = AtomicU16::new(0);
/// Last DesignCapacity (0x3C) read back from the gauge (0 = unknown).
static LAST_CHIP: AtomicU16 = AtomicU16::new(0);
static FAIL_UNTIL_US: Mutex<Cell<u64>> = Mutex::new(Cell::new(0));
static FAIL_LOGGED: AtomicBool = AtomicBool::new(false);

pub fn init() {
    // Nothing to seed: the gauge owns the value. State starts "unknown/none".
}

/// Build-time default, used only to seed a gauge whose data memory is blank.
pub fn baked_default() -> u16 {
    cfg::BATTERY_CAPACITY_MAH
}

/// Record the latest DesignCapacity readback (0x3C). When it matches a pending
/// target the change is confirmed and the pending request clears.
pub fn note_chip(mah: u16) {
    LAST_CHIP.store(mah, Ordering::Relaxed);
    let pending = PENDING_TARGET.load(Ordering::Relaxed);
    if pending != 0 && pending == mah {
        clear_pending();
    }
}

/// Queue a gauge reprogram on the sensor poll thread (I²C access). Returns true
/// when the request is valid/accepted (already-matching values are a no-op).
pub fn request_design_mah(mah: u16) -> bool {
    if !(MIN_DESIGN_MAH..=MAX_DESIGN_MAH).contains(&mah) {
        return false;
    }
    // Already what the gauge reports: nothing to do (and drop any stale pending).
    if mah == LAST_CHIP.load(Ordering::Relaxed) {
        clear_pending();
        return true;
    }
    PENDING_TARGET.store(mah, Ordering::Relaxed);
    FAIL_LOGGED.store(false, Ordering::Relaxed);
    critical_section::with(|cs| FAIL_UNTIL_US.borrow(cs).set(0));
    true
}

fn clear_pending() {
    PENDING_TARGET.store(0, Ordering::Relaxed);
    FAIL_LOGGED.store(false, Ordering::Relaxed);
    critical_section::with(|cs| FAIL_UNTIL_US.borrow(cs).set(0));
}

/// Mark a successful program: the gauge now reads `programmed` (verified by the
/// caller), so update the readback and clear the pending request.
pub fn note_program_ok(programmed: u16) {
    LAST_CHIP.store(programmed, Ordering::Relaxed);
    clear_pending();
}

pub fn note_program_fail(now_us: u64) {
    critical_section::with(|cs| {
        FAIL_UNTIL_US
            .borrow(cs)
            .set(now_us.saturating_add(PROGRAM_FAIL_BACKOFF_US));
    });
}

pub fn should_log_program_fail() -> bool {
    if FAIL_LOGGED.load(Ordering::Relaxed) {
        return false;
    }
    FAIL_LOGGED.store(true, Ordering::Relaxed);
    true
}

/// True once the gauge reports a valid configured design capacity and no change
/// is pending — used for SOC-based sleep policy.
pub fn gauge_trusted() -> bool {
    LAST_CHIP.load(Ordering::Relaxed) >= MIN_DESIGN_MAH
        && PENDING_TARGET.load(Ordering::Relaxed) == 0
}

/// Returns mAh to program when a pending target differs from the gauge readback
/// and we are not in fail-backoff. Pending is not cleared until verified by 0x3C.
pub fn should_program_design(now_us: u64) -> Option<u16> {
    let target = PENDING_TARGET.load(Ordering::Relaxed);
    if target == 0 || target == LAST_CHIP.load(Ordering::Relaxed) {
        return None;
    }
    critical_section::with(|cs| {
        let until = FAIL_UNTIL_US.borrow(cs).get();
        if until != 0 && now_us < until {
            return None;
        }
        Some(target)
    })
}
