//! Runtime LiPo design capacity for the BQ27441 gauge.
//!
//! The G1A data memory is RAM-only: a battery unplug (ITPOR) forgets Design
//! Capacity. Impedance Track seeds Qmax from Design Capacity — we do **not**
//! write State offset 0 (Qmax Cell 0 is 16384 Num, not mAh). The Pi still
//! Acks a Qmax SetAttr so config.json can remember last FCC; that value is
//! not programmed into the chip.

use core::cell::Cell;
use core::sync::atomic::{AtomicBool, AtomicU16, Ordering};

use critical_section::Mutex;

use crate::cfg;

/// Wait this long after a failed config update before retrying (µs).
const PROGRAM_FAIL_BACKOFF_US: u64 = 60_000_000;
/// Don't re-queue Design Capacity every sample if 0x3C is still factory.
const RESTORE_COOLDOWN_US: u64 = 60_000_000;

/// Plausible design-capacity bounds for a SetAttr request (mAh).
pub const MIN_DESIGN_MAH: u16 = 100;
const MAX_DESIGN_MAH: u16 = 32_000;

const MAX_PACKS: usize = 4;

#[derive(Clone, Copy)]
struct PackSlot {
    design: u16,
    qmax: u16,
}

/// Value the Pi most recently asked us to program (0 = nothing pending).
static PENDING_DESIGN: AtomicU16 = AtomicU16::new(0);
/// Last DesignCapacity (0x3C) read back from the gauge (0 = unknown).
static LAST_CHIP: AtomicU16 = AtomicU16::new(0);
static FAIL_UNTIL_US: Mutex<Cell<u64>> = Mutex::new(Cell::new(0));
static LAST_RESTORE_US: Mutex<Cell<u64>> = Mutex::new(Cell::new(0));
static FAIL_LOGGED: AtomicBool = AtomicBool::new(false);
static PACKS: Mutex<Cell<[PackSlot; MAX_PACKS]>> = Mutex::new(Cell::new(
    [PackSlot {
        design: 0,
        qmax: 0,
    }; MAX_PACKS],
));

pub fn init() {}

/// Build-time default, used only to seed a gauge whose data memory is blank.
pub fn baked_default() -> u16 {
    cfg::BATTERY_CAPACITY_MAH
}

/// Record the latest DesignCapacity readback (0x3C). When it matches a pending
/// target the change is confirmed and the pending request clears.
pub fn note_chip(mah: u16) {
    LAST_CHIP.store(mah, Ordering::Relaxed);
}

/// Pack size we intend to program (pending SetAttr, else the baked config).
/// Never treat a factory 1340 leftover on 0x3C as the intended pack.
fn intended_pack() -> u16 {
    let pending = PENDING_DESIGN.load(Ordering::Relaxed);
    if pending >= MIN_DESIGN_MAH {
        return pending;
    }
    baked_default()
}

/// If 0x3C is still the G1A factory leftover (or blank), queue a Design
/// Capacity rewrite. When 0x3C already matches the pack, leave Impedance
/// Track alone — CFGUPDATE every 60 s is what kept FCC from ever learning.
pub fn restore_if_fcc_implausible(chip_design: u16, fcc_mah: u16, now_us: u64) {
    let intended = intended_pack();
    if intended < MIN_DESIGN_MAH {
        return;
    }
    if chip_design == intended && plausible_qmax(intended, fcc_mah) {
        remember_qmax(intended, fcc_mah);
        return;
    }
    if chip_design == intended {
        return;
    }
    if PENDING_DESIGN.load(Ordering::Relaxed) != 0 {
        return;
    }
    if in_backoff(now_us) {
        return;
    }
    let since = critical_section::with(|cs| LAST_RESTORE_US.borrow(cs).get());
    if since != 0 && now_us.saturating_sub(since) < RESTORE_COOLDOWN_US {
        return;
    }
    critical_section::with(|cs| LAST_RESTORE_US.borrow(cs).set(now_us));
    PENDING_DESIGN.store(intended, Ordering::Relaxed);
}

fn in_backoff(now_us: u64) -> bool {
    critical_section::with(|cs| {
        let until = FAIL_UNTIL_US.borrow(cs).get();
        until != 0 && now_us < until
    })
}

/// Queue a gauge reprogram on the sensor poll thread (I²C access). Returns true
/// when the request is valid/accepted (already-matching values are a no-op).
pub fn request_design_mah(mah: u16) -> bool {
    if !(MIN_DESIGN_MAH..=MAX_DESIGN_MAH).contains(&mah) {
        return false;
    }
    PENDING_DESIGN.store(mah, Ordering::Relaxed);
    FAIL_LOGGED.store(false, Ordering::Relaxed);
    critical_section::with(|cs| FAIL_UNTIL_US.borrow(cs).set(0));
    true
}

/// Remember last learned FCC for this boot / Ack the Pi. Do not enter
/// CFGUPDATE — State Qmax Cell 0 is not an mAh seed.
pub fn request_qmax_mah(mah: u16) -> bool {
    if !(MIN_DESIGN_MAH..=MAX_DESIGN_MAH).contains(&mah) {
        return false;
    }
    let design = LAST_CHIP.load(Ordering::Relaxed);
    if design >= MIN_DESIGN_MAH {
        remember_qmax(design, mah);
    } else {
        remember_qmax(baked_default(), mah);
    }
    true
}

fn clear_pending() {
    PENDING_DESIGN.store(0, Ordering::Relaxed);
    FAIL_LOGGED.store(false, Ordering::Relaxed);
    critical_section::with(|cs| FAIL_UNTIL_US.borrow(cs).set(0));
}

/// Mark a successful program.
pub fn note_program_ok(design: u16, qmax: u16) {
    LAST_CHIP.store(design, Ordering::Relaxed);
    remember_qmax(design, qmax);
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

/// True when Design Capacity is programmed, nothing is pending, and live FCC
/// matches the configured pack — only then is SOC safe for Burst/Protect.
pub fn gauge_trusted(fcc_mah: u16) -> bool {
    let design = intended_pack();
    PENDING_DESIGN.load(Ordering::Relaxed) == 0 && plausible_qmax(design, fcc_mah)
}

/// Returns (design, qmax) to program when 0x3C differs from the pending
/// pack size. `qmax` is log-only — it is not written to State offset 0.
pub fn should_program_capacity(now_us: u64) -> Option<(u16, u16)> {
    let design = PENDING_DESIGN.load(Ordering::Relaxed);
    if design == 0 {
        return None;
    }
    let chip = LAST_CHIP.load(Ordering::Relaxed);
    if design < MIN_DESIGN_MAH {
        return None;
    }
    if design == chip {
        clear_pending();
        return None;
    }
    let qmax = lookup_qmax(design).unwrap_or(design);
    critical_section::with(|cs| {
        let until = FAIL_UNTIL_US.borrow(cs).get();
        if until != 0 && now_us < until {
            return None;
        }
        Some((design, qmax))
    })
}

fn plausible_qmax(design: u16, qmax: u16) -> bool {
    if design < MIN_DESIGN_MAH || qmax < MIN_DESIGN_MAH {
        return false;
    }
    let lo = (design as u32) * 70 / 100;
    let hi = (design as u32) * 120 / 100;
    let q = qmax as u32;
    q >= lo && q <= hi
}

fn remember_qmax(design: u16, qmax: u16) {
    critical_section::with(|cs| {
        let mut slots = PACKS.borrow(cs).get();
        if let Some(s) = slots.iter_mut().find(|s| s.design == design) {
            s.qmax = qmax;
            PACKS.borrow(cs).set(slots);
            return;
        }
        if let Some(s) = slots.iter_mut().find(|s| s.design == 0) {
            s.design = design;
            s.qmax = qmax;
            PACKS.borrow(cs).set(slots);
            return;
        }
        slots[0] = PackSlot {
            design,
            qmax,
        };
        PACKS.borrow(cs).set(slots);
    });
}

fn lookup_qmax(design: u16) -> Option<u16> {
    critical_section::with(|cs| {
        let slots = PACKS.borrow(cs).get();
        slots
            .iter()
            .find(|s| s.design == design && s.qmax >= MIN_DESIGN_MAH)
            .map(|s| s.qmax)
    })
}
