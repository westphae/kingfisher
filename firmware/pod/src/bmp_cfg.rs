//! Runtime BMP581 oversampling / IIR settings (applied on the sensor poll thread).

use core::sync::atomic::{AtomicU8, Ordering};

/// OSR register codes (osr_p / osr_t): 0=×1 … 7=×128.
const DEFAULT_OSR_P: u8 = 5; // ×32 — high resolution at 25 Hz ODR
const DEFAULT_OSR_T: u8 = 1; // ×2  — matches datasheet Table 9 for ×32 press
/// DSP_IIR register field codes: 0=bypass, 1=coeff1, 2=coeff3, …
const DEFAULT_IIR_P: u8 = 2; // filter coefficient 3
const DEFAULT_IIR_T: u8 = 2;

static OSR_P: AtomicU8 = AtomicU8::new(DEFAULT_OSR_P);
static OSR_T: AtomicU8 = AtomicU8::new(DEFAULT_OSR_T);
static IIR_P: AtomicU8 = AtomicU8::new(DEFAULT_IIR_P);
static IIR_T: AtomicU8 = AtomicU8::new(DEFAULT_IIR_T);
/// 1 = OSR/IIR changed since last apply (AtomicU8: bool::swap unavailable here).
static DIRTY: AtomicU8 = AtomicU8::new(1);

pub fn osr_p() -> u8 {
    OSR_P.load(Ordering::Relaxed)
}
pub fn osr_t() -> u8 {
    OSR_T.load(Ordering::Relaxed)
}
pub fn iir_p() -> u8 {
    IIR_P.load(Ordering::Relaxed)
}
pub fn iir_t() -> u8 {
    IIR_T.load(Ordering::Relaxed)
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

/// Map oversampling multiplier (1,2,4,…,128) → register code 0..7.
pub fn osr_mult_to_code(mult: u16) -> Option<u8> {
    match mult {
        1 => Some(0),
        2 => Some(1),
        4 => Some(2),
        8 => Some(3),
        16 => Some(4),
        32 => Some(5),
        64 => Some(6),
        128 => Some(7),
        _ => None,
    }
}

/// Map IIR filter coefficient (0,1,3,7,15,31,63,127) → register code 0..7.
pub fn iir_coeff_to_code(coeff: u16) -> Option<u8> {
    match coeff {
        0 => Some(0),
        1 => Some(1),
        3 => Some(2),
        7 => Some(3),
        15 => Some(4),
        31 => Some(5),
        63 => Some(6),
        127 => Some(7),
        _ => None,
    }
}

/// Max NORMAL-mode ODR (Hz) for a pressure OSR code (datasheet Table 9 continuous approx).
pub fn max_odr_hz_for_osr_p(osr_p_code: u8) -> u16 {
    match osr_p_code {
        0 => 240,
        1 => 218,
        2 => 199,
        3 => 155,
        4 => 87,
        5 => 46,
        6 => 24,
        _ => 12,
    }
}

pub fn request_osr_p_mult(mult: f32) -> bool {
    let Some(code) = osr_mult_to_code(mult as u16) else {
        return false;
    };
    let hz = crate::rates::get(pod_wire::SensorId::Static);
    if hz > max_odr_hz_for_osr_p(code) {
        return false;
    }
    OSR_P.store(code, Ordering::Relaxed);
    mark_dirty();
    true
}

pub fn request_osr_t_mult(mult: f32) -> bool {
    let Some(code) = osr_mult_to_code(mult as u16) else {
        return false;
    };
    OSR_T.store(code, Ordering::Relaxed);
    mark_dirty();
    true
}

pub fn request_iir_p_coeff(coeff: f32) -> bool {
    let Some(code) = iir_coeff_to_code(coeff as u16) else {
        return false;
    };
    IIR_P.store(code, Ordering::Relaxed);
    mark_dirty();
    true
}

pub fn request_iir_t_coeff(coeff: f32) -> bool {
    let Some(code) = iir_coeff_to_code(coeff as u16) else {
        return false;
    };
    IIR_T.store(code, Ordering::Relaxed);
    mark_dirty();
    true
}
