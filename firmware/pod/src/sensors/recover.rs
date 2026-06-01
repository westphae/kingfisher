//! I²C / sensor recovery after bus wedge or rate backoff.

use core::cell::RefCell;

use critical_section::Mutex;
use embassy_time::Instant;
use esp_println::println;

use super::bus::Bus;
use super::{SensorBoard, BATTERY_BIT, BMP_BIT, MMC_BIT, MS4525_BIT};
use crate::link;
use crate::rates;

/// Minimum time between full bus recoveries (µs).
const MIN_RECOVER_INTERVAL_US: u64 = 30_000_000;

static LAST_RECOVER_US: Mutex<RefCell<u64>> = Mutex::new(RefCell::new(0));

/// Re-init attached sensors. Keeps configured rates (Pi reapplies via Hello if needed).
pub fn recover_bus(bus: &mut Bus, board: &mut SensorBoard) {
    let now = Instant::now().as_micros();
    let skip = critical_section::with(|cs| {
        let last = *LAST_RECOVER_US.borrow(cs).borrow();
        if last != 0 && now.saturating_sub(last) < MIN_RECOVER_INTERVAL_US {
            true
        } else {
            *LAST_RECOVER_US.borrow(cs).borrow_mut() = now;
            false
        }
    });
    if skip {
        println!("pod: i2c recovery skipped (debounce)");
        rates::clear_overrun_streak();
        return;
    }
    println!("pod: i2c recovery");

    if let Some(ref mut bmp) = board.bmp581 {
        if bmp.init(bus).is_err() {
            println!("pod: bmp581 re-init failed; detaching");
            board.bmp581 = None;
        }
    }
    if let Some(ref mut mmc) = board.mmc5983 {
        if mmc.init(bus).is_err() {
            println!("pod: mmc5983 re-init failed; detaching");
            board.mmc5983 = None;
        }
    }
    if let Some(ref ms) = board.ms4525 {
        if ms.init(bus).is_err() {
            println!("pod: ms4525 re-init failed; detaching");
            board.ms4525 = None;
        }
    }
    if let Some(ref mut bq) = board.bq27441 {
        if bq.init(bus).is_err() {
            println!("pod: bq27441 re-init failed; detaching");
            board.bq27441 = None;
        }
    }

    let mut mask = 0u8;
    if board.bmp581.is_some() {
        mask |= BMP_BIT;
    }
    if board.mmc5983.is_some() {
        mask |= MMC_BIT;
    }
    if board.ms4525.is_some() {
        mask |= MS4525_BIT;
    }
    if board.bq27441.is_some() {
        mask |= BATTERY_BIT;
    }
    super::sync_attached_mask(mask);
    link::request_hello();
    rates::clear_overrun_streak();
}
