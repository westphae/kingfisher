//! Per-sensor sampling rates (Hz) and a shared I²C work budget per poll tick.
//!
//! BMP581 forced conversions take ~25 ms each; MMC5983 reads are comparatively
//! cheap. We cap total bus work per 100 ms tick, reject unsustainable SetRate
//! pairs at command time, and back off + recover when reads fail or a tick overruns.

use core::sync::atomic::{AtomicU16, AtomicU32, AtomicU8, Ordering};

use esp_println::println;
use pod_wire::SensorId;

use crate::cfg;
use crate::link;

/// Default sensor rates match [`crate::cfg::BASE_HZ`] (uplink / poll tick, 10 Hz).
const DEFAULT_STATIC_HZ: u16 = 10;
const DEFAULT_MAG_HZ: u16 = 10;
const DEFAULT_AIRSPEED_HZ: u16 = 10;
const DEFAULT_BATTERY_HZ: u16 = 1;
const SAFE_HZ: u16 = 10;
const SAFE_BATTERY_HZ: u16 = 1;

/// Per-sensor max reads when scheduled alone.
const MAX_READS_PER_SENSOR: u32 = 3;

/// BMP581 FIFO drain once per tick (count read + burst), excluding frame bytes.
const US_PER_STATIC_DRAIN: u32 = 3_000;
const US_PER_STATIC_FRAME: u32 = 400;
/// Status poll + 7-byte XOUT burst (no per-sample IC0 write).
const US_PER_MAG_READ: u32 = 1_200;
/// MS4525: trigger + ~2 ms conversion wait + I²C fetch (see `ms4525.rs`).
const US_PER_AIR_READ: u32 = 4_500;
const US_PER_BATTERY_READ: u32 = 3_000;

/// Max I²C work per 100 ms poll tick (leave time for UDP / WiFi).
const MAX_TICK_WORK_US: u32 = 100_000;

const FAIL_BACKOFF_THRESHOLD: u8 = 6;
const OVERRUN_BACKOFF_THRESHOLD: u8 = 2;

static STATIC_HZ: AtomicU16 = AtomicU16::new(DEFAULT_STATIC_HZ);
static MAG_HZ: AtomicU16 = AtomicU16::new(DEFAULT_MAG_HZ);
static AIRSPEED_HZ: AtomicU16 = AtomicU16::new(DEFAULT_AIRSPEED_HZ);
static BATTERY_HZ: AtomicU16 = AtomicU16::new(DEFAULT_BATTERY_HZ);

static STATIC_REM: AtomicU32 = AtomicU32::new(0);
static MAG_REM: AtomicU32 = AtomicU32::new(0);
static AIRSPEED_REM: AtomicU32 = AtomicU32::new(0);
static BATTERY_REM: AtomicU32 = AtomicU32::new(0);

static STATIC_FAIL: AtomicU8 = AtomicU8::new(0);
static MAG_FAIL: AtomicU8 = AtomicU8::new(0);
static AIRSPEED_FAIL: AtomicU8 = AtomicU8::new(0);
static BATTERY_FAIL: AtomicU8 = AtomicU8::new(0);

/// Hz before the most recent successful SetRate (for backoff).
static STATIC_PREV: AtomicU16 = AtomicU16::new(DEFAULT_STATIC_HZ);
static MAG_PREV: AtomicU16 = AtomicU16::new(DEFAULT_MAG_HZ);
static AIRSPEED_PREV: AtomicU16 = AtomicU16::new(DEFAULT_AIRSPEED_HZ);
static BATTERY_PREV: AtomicU16 = AtomicU16::new(DEFAULT_BATTERY_HZ);

static LAST_CHANGED: AtomicU8 = AtomicU8::new(0xff);
static OVERRUN_STREAK: AtomicU8 = AtomicU8::new(0);
static TICK_US_LEFT: AtomicU32 = AtomicU32::new(MAX_TICK_WORK_US);

fn hz_atom(sensor: SensorId) -> &'static AtomicU16 {
    match sensor {
        SensorId::Static => &STATIC_HZ,
        SensorId::Mag => &MAG_HZ,
        SensorId::Airspeed => &AIRSPEED_HZ,
        SensorId::Battery => &BATTERY_HZ,
    }
}

fn rem_atom(sensor: SensorId) -> &'static AtomicU32 {
    match sensor {
        SensorId::Static => &STATIC_REM,
        SensorId::Mag => &MAG_REM,
        SensorId::Airspeed => &AIRSPEED_REM,
        SensorId::Battery => &BATTERY_REM,
    }
}

fn fail_atom(sensor: SensorId) -> &'static AtomicU8 {
    match sensor {
        SensorId::Static => &STATIC_FAIL,
        SensorId::Mag => &MAG_FAIL,
        SensorId::Airspeed => &AIRSPEED_FAIL,
        SensorId::Battery => &BATTERY_FAIL,
    }
}

fn prev_atom(sensor: SensorId) -> &'static AtomicU16 {
    match sensor {
        SensorId::Static => &STATIC_PREV,
        SensorId::Mag => &MAG_PREV,
        SensorId::Airspeed => &AIRSPEED_PREV,
        SensorId::Battery => &BATTERY_PREV,
    }
}

fn us_per_read(sensor: SensorId) -> u32 {
    match sensor {
        SensorId::Static => US_PER_STATIC_DRAIN,
        SensorId::Mag => US_PER_MAG_READ,
        SensorId::Airspeed => US_PER_AIR_READ,
        SensorId::Battery => US_PER_BATTERY_READ,
    }
}

fn sensor_from_tag(tag: u8) -> Option<SensorId> {
    match tag {
        0 => Some(SensorId::Static),
        1 => Some(SensorId::Mag),
        2 => Some(SensorId::Airspeed),
        3 => Some(SensorId::Battery),
        _ => None,
    }
}

fn tag_from_sensor(sensor: SensorId) -> u8 {
    match sensor {
        SensorId::Static => 0,
        SensorId::Mag => 1,
        SensorId::Airspeed => 2,
        SensorId::Battery => 3,
    }
}

pub fn get(sensor: SensorId) -> u16 {
    hz_atom(sensor).load(Ordering::Relaxed)
}

fn store_hz(sensor: SensorId, hz: u16) {
    hz_atom(sensor).store(hz, Ordering::Relaxed);
}

fn desired_reads(sensor: SensorId, hz: u16) -> u32 {
    if hz == 0 {
        return 0;
    }
    let tick_hz = cfg::BASE_HZ as u32;
    let rem = rem_atom(sensor);
    let new_rem = rem.load(Ordering::Relaxed) + hz as u32;
    let mut take = new_rem / tick_hz;
    if take > MAX_READS_PER_SENSOR {
        take = MAX_READS_PER_SENSOR;
    }
    rem.store(new_rem - take * tick_hz, Ordering::Relaxed);
    take
}

fn reads_per_tick(hz: u16) -> u64 {
    if hz == 0 {
        return 0;
    }
    let base = cfg::BASE_HZ as u64;
    ((hz as u64) + base - 1) / base
}

fn reads_per_tick_capped(hz: u16) -> u64 {
    let r = reads_per_tick(hz);
    r.min(MAX_READS_PER_SENSOR as u64)
}

fn static_tick_work_us(hz: u16) -> u64 {
    if hz == 0 {
        return 0;
    }
    let frames = reads_per_tick(hz).max(1);
    US_PER_STATIC_DRAIN as u64 + frames * US_PER_STATIC_FRAME as u64
}

/// Estimated blocking time for one tick (BMP FIFO drain; others capped).
fn tick_work_us(static_hz: u16, mag_hz: u16, air_hz: u16, battery_hz: u16) -> u64 {
    static_tick_work_us(static_hz)
        + reads_per_tick_capped(mag_hz) * US_PER_MAG_READ as u64
        + reads_per_tick_capped(air_hz) * US_PER_AIR_READ as u64
        + reads_per_tick_capped(battery_hz) * US_PER_BATTERY_READ as u64
}

/// Whether these rates fit the shared bus time budget (before accepting SetRate).
fn sustainable(static_hz: u16, mag_hz: u16, air_hz: u16, battery_hz: u16) -> bool {
    tick_work_us(static_hz, mag_hz, air_hz, battery_hz) <= MAX_TICK_WORK_US as u64
}

/// Apply SetRate if the combined schedule is sustainable; records previous Hz.
pub fn try_set(sensor: SensorId, hz: u16) -> bool {
    let (s, m, a, b) = match sensor {
        SensorId::Static => (
            hz,
            get(SensorId::Mag),
            get(SensorId::Airspeed),
            get(SensorId::Battery),
        ),
        SensorId::Mag => (
            get(SensorId::Static),
            hz,
            get(SensorId::Airspeed),
            get(SensorId::Battery),
        ),
        SensorId::Airspeed => (
            get(SensorId::Static),
            get(SensorId::Mag),
            hz,
            get(SensorId::Battery),
        ),
        SensorId::Battery => (
            get(SensorId::Static),
            get(SensorId::Mag),
            get(SensorId::Airspeed),
            hz,
        ),
    };
    if !sustainable(s, m, a, b) {
        return false;
    }
    prev_atom(sensor).store(get(sensor), Ordering::Relaxed);
    store_hz(sensor, hz);
    fail_atom(sensor).store(0, Ordering::Relaxed);
    LAST_CHANGED.store(tag_from_sensor(sensor), Ordering::Relaxed);
    true
}

pub fn set_safe_defaults() {
    store_hz(SensorId::Static, SAFE_HZ);
    store_hz(SensorId::Mag, SAFE_HZ);
    store_hz(SensorId::Airspeed, SAFE_HZ);
    store_hz(SensorId::Battery, SAFE_BATTERY_HZ);
    for s in [
        SensorId::Static,
        SensorId::Mag,
        SensorId::Airspeed,
        SensorId::Battery,
    ] {
        rem_atom(s).store(0, Ordering::Relaxed);
        fail_atom(s).store(0, Ordering::Relaxed);
    }
    OVERRUN_STREAK.store(0, Ordering::Relaxed);
    link::request_hello();
}

/// Max Hz to advertise in Hello for a sensor given what else is attached.
pub fn hello_max_hz(sensor: SensorId, attached: u8) -> u16 {
    use crate::sensors::{BATTERY_BIT, BMP_BIT, MMC_BIT, MS4525_BIT};
    let mut s = if attached & BMP_BIT != 0 {
        get(SensorId::Static)
    } else {
        0
    };
    let mut m = if attached & MMC_BIT != 0 {
        get(SensorId::Mag)
    } else {
        0
    };
    let mut a = if attached & MS4525_BIT != 0 {
        get(SensorId::Airspeed)
    } else {
        0
    };
    let mut b = if attached & BATTERY_BIT != 0 {
        get(SensorId::Battery)
    } else {
        0
    };
    match sensor {
        SensorId::Static => s = 50,
        SensorId::Mag => m = 50,
        SensorId::Airspeed => a = 50,
        SensorId::Battery => b = 2,
    }
    let mut cap = match sensor {
        SensorId::Battery => 2u16,
        _ => 50u16,
    };
    while cap > 0 {
        let (ts, tm, ta, tb) = match sensor {
            SensorId::Static => (cap, m, a, b),
            SensorId::Mag => (s, cap, a, b),
            SensorId::Airspeed => (s, m, cap, b),
            SensorId::Battery => (s, m, a, cap),
        };
        if sustainable(ts, tm, ta, tb) {
            return cap;
        }
        cap = cap.saturating_sub(1);
    }
    match sensor {
        SensorId::Battery => SAFE_BATTERY_HZ,
        _ => SAFE_HZ,
    }
}

/// Start of each poll tick: reset the shared microsecond work pool.
pub fn begin_tick() {
    TICK_US_LEFT.store(MAX_TICK_WORK_US, Ordering::Relaxed);
}

/// Plan reads for one sensor (cheap sensors first in the poll loop).
pub fn poll_budget(sensor: SensorId, hz: u16) -> u32 {
    let want = desired_reads(sensor, hz);
    if want == 0 {
        return 0;
    }
    let unit = us_per_read(sensor);
    let left = TICK_US_LEFT.load(Ordering::Relaxed);
    if left < unit {
        return 0;
    }
    let max_by_time = left / unit;
    let take = want.min(max_by_time).min(MAX_READS_PER_SENSOR);
    if take > 0 {
        TICK_US_LEFT.store(left - take * unit, Ordering::Relaxed);
    }
    take
}

pub fn note_read_ok(sensor: SensorId) {
    fail_atom(sensor).store(0, Ordering::Relaxed);
}

/// True when the caller should run bus recovery (rate was lowered).
pub fn note_read_fail(sensor: SensorId) -> bool {
    let f = fail_atom(sensor).load(Ordering::Relaxed).saturating_add(1);
    fail_atom(sensor).store(f, Ordering::Relaxed);
    println!(
        "pod: {:?} read fail ({}/{})",
        sensor, f, FAIL_BACKOFF_THRESHOLD
    );
    if f >= FAIL_BACKOFF_THRESHOLD {
        return backoff(sensor);
    }
    false
}

pub fn note_tick_overrun() -> bool {
    let n = OVERRUN_STREAK.load(Ordering::Relaxed).saturating_add(1);
    OVERRUN_STREAK.store(n, Ordering::Relaxed);
    if n >= OVERRUN_BACKOFF_THRESHOLD {
        println!(
            "pod: poll tick overrun ({} consecutive ticks >80 ms)",
            n
        );
        OVERRUN_STREAK.store(0, Ordering::Relaxed);
        if let Some(s) = sensor_from_tag(LAST_CHANGED.load(Ordering::Relaxed)) {
            return backoff(s);
        }
        set_safe_defaults();
        return true;
    }
    false
}

pub fn clear_overrun_streak() {
    OVERRUN_STREAK.store(0, Ordering::Relaxed);
}

/// Lower the sensor rate after repeated read failures. Returns true only when Hz
/// actually changed (caller may run bus recovery). At the safe floor, log once
/// and do not trigger recovery — transient MS4525 NACKs are common under load.
fn backoff(sensor: SensorId) -> bool {
    let prev = prev_atom(sensor).load(Ordering::Relaxed);
    let cur = get(sensor);
    let next = if prev > 0 && prev < cur {
        prev
    } else {
        match sensor {
            SensorId::Battery => (cur / 2).max(SAFE_BATTERY_HZ),
            _ => (cur / 2).max(SAFE_HZ),
        }
    };
    rem_atom(sensor).store(0, Ordering::Relaxed);
    fail_atom(sensor).store(0, Ordering::Relaxed);
    if next >= cur {
        println!(
            "pod: {:?} read errors at {} Hz (already at safe minimum)",
            sensor, cur
        );
        return false;
    }
    println!(
        "pod: backing off {:?} {} -> {} Hz",
        sensor, cur, next
    );
    store_hz(sensor, next);
    link::request_hello();
    true
}
