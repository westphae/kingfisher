//! Runtime LiPo design capacity and Qmax for the BQ27441 gauge.
//!
//! The G1A data memory is RAM-only: a battery unplug (ITPOR) forgets Design
//! Capacity and the learned Qmax. We keep a small per-pack table of last-
//! known FullChargeCapacity (used as the Qmax seed) in RAM this boot; the Pi
//! persists the same map in config.json and restores it over SetAttr on Hello.

use core::cell::Cell;
use core::sync::atomic::{AtomicBool, AtomicU16, Ordering};

use critical_section::Mutex;

use crate::cfg;

/// Wait this long after a failed config update before retrying (µs).
const PROGRAM_FAIL_BACKOFF_US: u64 = 60_000_000;
/// Don't re-seed Qmax every sample if FCC stays implausible after a write.
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
static PENDING_QMAX: AtomicU16 = AtomicU16::new(0);
/// Last DesignCapacity (0x3C) read back from the gauge (0 = unknown).
static LAST_CHIP: AtomicU16 = AtomicU16::new(0);
/// Last Qmax we wrote this boot (0 = never written).
static LAST_QMAX: AtomicU16 = AtomicU16::new(0);
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

/// If live FCC does not match the configured pack, queue a Qmax restore
/// (last learned, else design). No-ops while a program is already pending or
/// in fail-backoff so we do not hammer CONFIG UPDATE every sample.
pub fn restore_if_fcc_implausible(design_mah: u16, fcc_mah: u16, now_us: u64) {
    if design_mah < MIN_DESIGN_MAH {
        return;
    }
    if plausible_qmax(design_mah, fcc_mah) {
        remember_qmax(design_mah, fcc_mah);
        return;
    }
    if PENDING_DESIGN.load(Ordering::Relaxed) != 0 || PENDING_QMAX.load(Ordering::Relaxed) != 0 {
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
    LAST_QMAX.store(0, Ordering::Relaxed);
    let qmax = lookup_qmax(design_mah).unwrap_or(design_mah);
    PENDING_DESIGN.store(design_mah, Ordering::Relaxed);
    PENDING_QMAX.store(qmax, Ordering::Relaxed);
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
    if PENDING_QMAX.load(Ordering::Relaxed) == 0 {
        if let Some(q) = lookup_qmax(mah) {
            PENDING_QMAX.store(q, Ordering::Relaxed);
        }
    }
    FAIL_LOGGED.store(false, Ordering::Relaxed);
    critical_section::with(|cs| FAIL_UNTIL_US.borrow(cs).set(0));
    true
}

/// Queue a Qmax restore (learned FullChargeCapacity for the selected pack).
pub fn request_qmax_mah(mah: u16) -> bool {
    if !(MIN_DESIGN_MAH..=MAX_DESIGN_MAH).contains(&mah) {
        return false;
    }
    PENDING_QMAX.store(mah, Ordering::Relaxed);
    FAIL_LOGGED.store(false, Ordering::Relaxed);
    critical_section::with(|cs| FAIL_UNTIL_US.borrow(cs).set(0));
    true
}

fn clear_pending() {
    PENDING_DESIGN.store(0, Ordering::Relaxed);
    PENDING_QMAX.store(0, Ordering::Relaxed);
    FAIL_LOGGED.store(false, Ordering::Relaxed);
    critical_section::with(|cs| FAIL_UNTIL_US.borrow(cs).set(0));
}

/// Mark a successful program.
pub fn note_program_ok(design: u16, qmax: u16) {
    LAST_CHIP.store(design, Ordering::Relaxed);
    LAST_QMAX.store(qmax, Ordering::Relaxed);
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
/// matches that pack — only then is SOC safe for Burst/Protect.
pub fn gauge_trusted(fcc_mah: u16) -> bool {
    let design = LAST_CHIP.load(Ordering::Relaxed);
    PENDING_DESIGN.load(Ordering::Relaxed) == 0
        && PENDING_QMAX.load(Ordering::Relaxed) == 0
        && plausible_qmax(design, fcc_mah)
}

/// Returns (design, qmax) to program when a pending target differs from the
/// last write and we are not in fail-backoff.
pub fn should_program_capacity(now_us: u64) -> Option<(u16, u16)> {
    let design = PENDING_DESIGN.load(Ordering::Relaxed);
    let qmax_req = PENDING_QMAX.load(Ordering::Relaxed);
    if design == 0 && qmax_req == 0 {
        return None;
    }
    let chip = LAST_CHIP.load(Ordering::Relaxed);
    let target_design = if design != 0 { design } else { chip };
    if target_design < MIN_DESIGN_MAH {
        return None;
    }
    let target_qmax = if qmax_req != 0 {
        qmax_req
    } else if let Some(q) = lookup_qmax(target_design) {
        q
    } else {
        target_design
    };
    if target_design == chip
        && target_qmax == LAST_QMAX.load(Ordering::Relaxed)
        && LAST_QMAX.load(Ordering::Relaxed) != 0
    {
        clear_pending();
        return None;
    }
    critical_section::with(|cs| {
        let until = FAIL_UNTIL_US.borrow(cs).get();
        if until != 0 && now_us < until {
            return None;
        }
        Some((target_design, target_qmax))
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
