//! Per-sensor sampling rates (Hz), updated by inbound `SetRate` commands.

use core::sync::atomic::{AtomicU16, Ordering};

use pod_wire::SensorId;

use crate::cfg;

const DEFAULT_STATIC_HZ: u16 = 10;
const DEFAULT_MAG_HZ: u16 = 10;
const DEFAULT_AIRSPEED_HZ: u16 = 10;

static STATIC_HZ: AtomicU16 = AtomicU16::new(DEFAULT_STATIC_HZ);
static MAG_HZ: AtomicU16 = AtomicU16::new(DEFAULT_MAG_HZ);
static AIRSPEED_HZ: AtomicU16 = AtomicU16::new(DEFAULT_AIRSPEED_HZ);

pub fn set(sensor: SensorId, hz: u16) {
    match sensor {
        SensorId::Static => STATIC_HZ.store(hz, Ordering::Relaxed),
        SensorId::Mag => MAG_HZ.store(hz, Ordering::Relaxed),
        SensorId::Airspeed => AIRSPEED_HZ.store(hz, Ordering::Relaxed),
    }
}

pub fn get(sensor: SensorId) -> u16 {
    match sensor {
        SensorId::Static => STATIC_HZ.load(Ordering::Relaxed),
        SensorId::Mag => MAG_HZ.load(Ordering::Relaxed),
        SensorId::Airspeed => AIRSPEED_HZ.load(Ordering::Relaxed),
    }
}

/// Scheduler runs at [`cfg::BASE_HZ`] (100 ms tick). Returns how many I²C reads
/// to perform this tick for the requested rate.
pub fn reads_this_tick(hz: u16) -> u32 {
    if hz == 0 {
        return 0;
    }
    let base = cfg::BASE_HZ as u32;
    let h = hz as u32;
    if h <= base {
        1
    } else {
        (h + base - 1) / base
    }
}

/// When `hz <= BASE_HZ`, only poll on ticks where this returns true.
pub fn should_poll(tick: u32, hz: u16) -> bool {
    if hz == 0 {
        return false;
    }
    let base = cfg::BASE_HZ as u32;
    let h = hz as u32;
    if h > base {
        return true;
    }
    let period = (base / h).max(1);
    tick % period == 0
}
