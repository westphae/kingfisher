//! Runtime LiPo design capacity (mAh) for BQ27441 programming.
//!
//! Build-time default comes from [`crate::cfg::BATTERY_CAPACITY_MAH`]; Pi may
//! override via `Cmd::SetAttr` / `AttrKey::DesignCapacity`.

use core::cell::Cell;
use core::sync::atomic::{AtomicBool, AtomicU16, Ordering};

use critical_section::Mutex;

use crate::cfg;

/// Wait this long after a failed config update before retrying (µs).
const PROGRAM_FAIL_BACKOFF_US: u64 = 60_000_000;

static DESIGN_MAH: AtomicU16 = AtomicU16::new(0);
static PENDING_MAH: Mutex<Cell<u16>> = Mutex::new(Cell::new(0));
static PROGRAM_DONE: AtomicBool = AtomicBool::new(false);
static FAIL_UNTIL_US: Mutex<Cell<u64>> = Mutex::new(Cell::new(0));
static FAIL_LOGGED: AtomicBool = AtomicBool::new(false);

pub fn init() {
    DESIGN_MAH.store(cfg::BATTERY_CAPACITY_MAH, Ordering::Relaxed);
}

pub fn design_mah() -> u16 {
    let v = DESIGN_MAH.load(Ordering::Relaxed);
    if v == 0 {
        cfg::BATTERY_CAPACITY_MAH
    } else {
        v
    }
}

/// Queue a gauge reprogram on the sensor poll thread (I²C access).
pub fn request_design_mah(mah: u16) -> bool {
    if mah < 100 {
        return false;
    }
    if PROGRAM_DONE.load(Ordering::Relaxed) && design_mah() == mah {
        return true;
    }
    // Duplicate SetAttr while already queued or in backoff: do not reset the timer.
    let duplicate = critical_section::with(|cs| PENDING_MAH.borrow(cs).get() == mah);
    if duplicate {
        return true;
    }
    DESIGN_MAH.store(mah, Ordering::Relaxed);
    PROGRAM_DONE.store(false, Ordering::Relaxed);
    FAIL_LOGGED.store(false, Ordering::Relaxed);
    critical_section::with(|cs| {
        PENDING_MAH.borrow(cs).set(mah);
        FAIL_UNTIL_US.borrow(cs).set(0);
    });
    true
}

/// Mark design capacity as successfully programmed (no further attempts).
pub fn note_program_ok() {
    PROGRAM_DONE.store(true, Ordering::Relaxed);
    critical_section::with(|cs| {
        PENDING_MAH.borrow(cs).set(0);
        FAIL_UNTIL_US.borrow(cs).set(0);
    });
    FAIL_LOGGED.store(false, Ordering::Relaxed);
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

/// True when a full-capacity read shows the gauge already learned design capacity.
pub fn note_gauge_full_capacity(full_mah: f32) {
    if full_mah > 10.0 {
        note_program_ok();
    }
}

/// Returns mAh to program when pending and not in backoff; does not clear pending until ok.
pub fn should_program_design(now_us: u64) -> Option<u16> {
    if PROGRAM_DONE.load(Ordering::Relaxed) {
        return None;
    }
    critical_section::with(|cs| {
        let until = FAIL_UNTIL_US.borrow(cs).get();
        if until != 0 && now_us < until {
            return None;
        }
        let mah = PENDING_MAH.borrow(cs).get();
        if mah >= 100 {
            Some(mah)
        } else {
            None
        }
    })
}
