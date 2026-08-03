//! Runtime MMC5983 bandwidth (IC1 BW[1:0]).

use core::sync::atomic::{AtomicU8, Ordering};

/// Datasheet BW filter labels (Hz): 100 / 200 / 400 / 800 → codes 0..3.
/// Default BW=100 (code 0) is lowest RMS noise (0.4 mG).
static BW_CODE: AtomicU8 = AtomicU8::new(0);
/// 1 = BW changed since last apply.
static DIRTY: AtomicU8 = AtomicU8::new(1);

pub fn bw_code() -> u8 {
    BW_CODE.load(Ordering::Relaxed)
}

pub fn bw_hz() -> u16 {
    match bw_code() {
        0 => 100,
        1 => 200,
        2 => 400,
        _ => 800,
    }
}

pub fn take_dirty() -> bool {
    if DIRTY.load(Ordering::Relaxed) == 0 {
        return false;
    }
    DIRTY.store(0, Ordering::Relaxed);
    true
}

pub fn mark_dirty() {
    DIRTY.store(1, Ordering::Relaxed);
}

pub fn bw_hz_to_code(hz: u16) -> Option<u8> {
    match hz {
        100 => Some(0),
        200 => Some(1),
        400 => Some(2),
        800 => Some(3),
        _ => None,
    }
}

/// Max continuous ODR (Hz) for a BW code (datasheet max-ODR + CM_Freq tables).
pub fn max_odr_hz_for_bw(bw_code: u8) -> u16 {
    match bw_code {
        0 => 100,
        1 => 100,
        2 => 200,
        _ => 1000,
    }
}

pub fn request_bw_hz(hz: f32) -> bool {
    let Some(code) = bw_hz_to_code(hz as u16) else {
        return false;
    };
    let rate = crate::rates::get(pod_wire::SensorId::Mag);
    if rate > max_odr_hz_for_bw(code) {
        return false;
    }
    BW_CODE.store(code, Ordering::Relaxed);
    mark_dirty();
    true
}
