//! Burst-mode sample store: compact per-sensor rings that hold readings while
//! the radio is off, drained through normal wire batches on reconnect.
//!
//! Entries keep the full `captured_us` uptime stamp; `build_batch` converts to
//! the wire's per-reading `age_us` against a fresh `pod_uptime_us` at drain
//! time, so the Pi's clock-offset EMA sees normal (just old) readings.
//!
//! Rings are sized for 50 Hz mag/static (25 Hz airspeed) over one burst
//! window. When a ring fills (uplink failing, or rates above 50 Hz), the
//! oldest entry is overwritten and counted — newest data always wins.

use core::cell::RefCell;

use critical_section::Mutex;
use heapless::{Deque, Vec};
use pod_wire::{Reading, SampleBatch, MAX_READINGS};

use crate::cfg;

const MAG_CAP: usize = cfg::BURST_WINDOW_S * 50;
const STATIC_CAP: usize = cfg::BURST_WINDOW_S * 50;
const AIRSPEED_CAP: usize = cfg::BURST_WINDOW_S * 25;
const BATTERY_CAP: usize = cfg::BURST_WINDOW_S * 2;

/// Start an uplink early when any ring is this full (rates above the sizing
/// assumption must shorten the window, not lose data).
const NEARLY_FULL_NUM: usize = 9;
const NEARLY_FULL_DEN: usize = 10;

struct MagEntry {
    captured_us: u64,
    x_ut: f32,
    y_ut: f32,
    z_ut: f32,
}

struct StaticEntry {
    captured_us: u64,
    p_pa: f32,
    temp_c: f32,
}

struct AirspeedEntry {
    captured_us: u64,
    dp_pa: f32,
    temp_c: f32,
}

struct BatteryEntry {
    captured_us: u64,
    voltage_v: f32,
    current_a: f32,
    power_w: f32,
    capacity_remain_mah: f32,
    capacity_full_mah: f32,
    soc_pct: f32,
    time_remain_s: f32,
    design_capacity_mah: u16,
}

struct Store {
    mag: Deque<MagEntry, MAG_CAP>,
    static_: Deque<StaticEntry, STATIC_CAP>,
    airspeed: Deque<AirspeedEntry, AIRSPEED_CAP>,
    battery: Deque<BatteryEntry, BATTERY_CAP>,
    overwritten: u32,
}

static STORE: Mutex<RefCell<Store>> = Mutex::new(RefCell::new(Store {
    mag: Deque::new(),
    static_: Deque::new(),
    airspeed: Deque::new(),
    battery: Deque::new(),
    overwritten: 0,
}));

fn with<R>(f: impl FnOnce(&mut Store) -> R) -> R {
    critical_section::with(|cs| f(&mut STORE.borrow(cs).borrow_mut()))
}

/// Total buffered readings across all rings.
pub fn depth() -> u16 {
    with(|s| {
        (s.mag.len() + s.static_.len() + s.airspeed.len() + s.battery.len())
            .min(u16::MAX as usize) as u16
    })
}

/// Oldest-overwritten count (data genuinely lost to ring wrap).
pub fn overwritten() -> u32 {
    with(|s| s.overwritten)
}

/// True when any ring is ≥90% full — time to force an uplink window.
pub fn nearly_full() -> bool {
    with(|s| {
        s.mag.len() * NEARLY_FULL_DEN >= MAG_CAP * NEARLY_FULL_NUM
            || s.static_.len() * NEARLY_FULL_DEN >= STATIC_CAP * NEARLY_FULL_NUM
            || s.airspeed.len() * NEARLY_FULL_DEN >= AIRSPEED_CAP * NEARLY_FULL_NUM
            || s.battery.len() * NEARLY_FULL_DEN >= BATTERY_CAP * NEARLY_FULL_NUM
    })
}

/// Push a reading captured at `captured_us`. Only the reading kinds the wire
/// knows are storable; the enum match is total so a new Reading variant fails
/// the build here instead of silently dropping.
pub fn push(reading: &Reading, captured_us: u64) {
    with(|s| match *reading {
        Reading::Mag {
            x_ut, y_ut, z_ut, ..
        } => {
            if s.mag.is_full() {
                let _ = s.mag.pop_front();
                s.overwritten = s.overwritten.saturating_add(1);
            }
            let _ = s.mag.push_back(MagEntry {
                captured_us,
                x_ut,
                y_ut,
                z_ut,
            });
        }
        Reading::Static { p_pa, temp_c, .. } => {
            if s.static_.is_full() {
                let _ = s.static_.pop_front();
                s.overwritten = s.overwritten.saturating_add(1);
            }
            let _ = s.static_.push_back(StaticEntry {
                captured_us,
                p_pa,
                temp_c,
            });
        }
        Reading::Airspeed { dp_pa, temp_c, .. } => {
            if s.airspeed.is_full() {
                let _ = s.airspeed.pop_front();
                s.overwritten = s.overwritten.saturating_add(1);
            }
            let _ = s.airspeed.push_back(AirspeedEntry {
                captured_us,
                dp_pa,
                temp_c,
            });
        }
        Reading::Battery {
            voltage_v,
            current_a,
            power_w,
            capacity_remain_mah,
            capacity_full_mah,
            soc_pct,
            time_remain_s,
            design_capacity_mah,
            ..
        } => {
            if s.battery.is_full() {
                let _ = s.battery.pop_front();
                s.overwritten = s.overwritten.saturating_add(1);
            }
            let _ = s.battery.push_back(BatteryEntry {
                captured_us,
                voltage_v,
                current_a,
                power_w,
                capacity_remain_mah,
                capacity_full_mah,
                soc_pct,
                time_remain_s,
                design_capacity_mah,
            });
        }
    });
}

fn age_us(pod_uptime_us: u64, captured_us: u64) -> u32 {
    pod_uptime_us
        .saturating_sub(captured_us)
        .min(u32::MAX as u64) as u32
}

/// Build one wire batch from the rings, oldest-first, round-robining sensors
/// like `LatestSamples::build_batch`. Empty batch means fully drained.
pub fn build_batch(pod_uptime_us: u64, seq: u32) -> SampleBatch {
    let mut samples: Vec<Reading, MAX_READINGS> = Vec::new();
    with(|s| {
        while samples.len() < MAX_READINGS {
            let before = samples.len();
            if samples.len() < MAX_READINGS {
                if let Some(e) = s.static_.pop_front() {
                    let _ = samples.push(Reading::Static {
                        p_pa: e.p_pa,
                        temp_c: e.temp_c,
                        age_us: age_us(pod_uptime_us, e.captured_us),
                    });
                }
            }
            if samples.len() < MAX_READINGS {
                if let Some(e) = s.mag.pop_front() {
                    let _ = samples.push(Reading::Mag {
                        x_ut: e.x_ut,
                        y_ut: e.y_ut,
                        z_ut: e.z_ut,
                        age_us: age_us(pod_uptime_us, e.captured_us),
                    });
                }
            }
            if samples.len() < MAX_READINGS {
                if let Some(e) = s.airspeed.pop_front() {
                    let _ = samples.push(Reading::Airspeed {
                        dp_pa: e.dp_pa,
                        temp_c: e.temp_c,
                        age_us: age_us(pod_uptime_us, e.captured_us),
                    });
                }
            }
            if samples.len() < MAX_READINGS {
                if let Some(e) = s.battery.pop_front() {
                    let _ = samples.push(Reading::Battery {
                        voltage_v: e.voltage_v,
                        current_a: e.current_a,
                        power_w: e.power_w,
                        capacity_remain_mah: e.capacity_remain_mah,
                        capacity_full_mah: e.capacity_full_mah,
                        soc_pct: e.soc_pct,
                        time_remain_s: e.time_remain_s,
                        design_capacity_mah: e.design_capacity_mah,
                        age_us: age_us(pod_uptime_us, e.captured_us),
                    });
                }
            }
            if samples.len() == before {
                break;
            }
        }
    });
    SampleBatch {
        pod_uptime_us,
        seq,
        samples,
    }
}
