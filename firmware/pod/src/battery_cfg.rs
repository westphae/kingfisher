//! Runtime LiPo design capacity (mAh) for BQ27441 programming.
//!
//! Build-time default comes from [`crate::cfg::BATTERY_CAPACITY_MAH`]; Pi may
//! override via `Cmd::SetAttr` / `AttrKey::DesignCapacity`.

use core::cell::Cell;
use core::sync::atomic::{AtomicU16, Ordering};

use critical_section::Mutex;

use crate::cfg;

static DESIGN_MAH: AtomicU16 = AtomicU16::new(0);
static PENDING_MAH: Mutex<Cell<u16>> = Mutex::new(Cell::new(0));

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
    DESIGN_MAH.store(mah, Ordering::Relaxed);
    critical_section::with(|cs| {
        PENDING_MAH.borrow(cs).set(mah);
    });
    true
}

pub fn take_pending_design_mah() -> Option<u16> {
    critical_section::with(|cs| {
        let cell = PENDING_MAH.borrow(cs);
        let v = cell.get();
        if v == 0 {
            None
        } else {
            cell.set(0);
            Some(v)
        }
    })
}
