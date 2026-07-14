//! Sensor drivers and latest-sample state shared with the uplink task.

pub mod bmp581;
pub mod bq27441;
pub mod bus;
pub mod mmc5983;
pub mod ms4525;
mod recover;

use core::cell::RefCell;
use core::sync::atomic::{AtomicU8, Ordering};

use critical_section::Mutex;
use embassy_time::Instant;
use heapless::Vec;
use pod_wire::{Reading, SampleBatch, MAX_READINGS};

use bmp581::Bmp581;
use bq27441::Bq27441;
use bus::Bus;
use esp_println::{print, println};
use mmc5983::Mmc5983;
use ms4525::Ms4525;

use crate::link;
use crate::power;
use crate::rates;

pub const BMP_BIT: u8 = 1;
pub const MMC_BIT: u8 = 2;
pub const MS4525_BIT: u8 = 4;
pub const BATTERY_BIT: u8 = 8;

static LATEST_BATTERY_V: core::sync::atomic::AtomicU32 = core::sync::atomic::AtomicU32::new(0);
static DROPPED_READINGS: Mutex<RefCell<u32>> = Mutex::new(RefCell::new(0));

const MAX_BUFFERED_READINGS: usize = crate::cfg::BUFFER_MAX_READINGS as usize;

/// Latest fuel-gauge voltage for Status frames (0 when unknown).
pub fn latest_battery_v() -> f32 {
    let bits = LATEST_BATTERY_V.load(core::sync::atomic::Ordering::Relaxed);
    if bits == 0 {
        return 0.0;
    }
    f32::from_bits(bits)
}

/// Depth of the live pending queues only (excludes the burst store).
pub fn pending_depth() -> u16 {
    with_samples_mut(|s| {
        let n = s.pending_static.len()
            + s.pending_mag.len()
            + s.pending_airspeed.len()
            + s.pending_battery.len();
        n.min(u16::MAX as usize) as u16
    })
}

/// Total buffered readings (pending queues + burst store) for Status frames.
pub fn buffer_depth() -> u16 {
    pending_depth().saturating_add(crate::burst::depth())
}

pub fn dropped_readings() -> u32 {
    critical_section::with(|cs| *DROPPED_READINGS.borrow(cs).borrow())
}

fn note_dropped_reading(sensor: &'static str) {
    critical_section::with(|cs| {
        let mut n = DROPPED_READINGS.borrow(cs).borrow_mut();
        *n = n.saturating_add(1);
    });
    println!("pod: warn: dropped {} sample (buffer full)", sensor);
}

fn store_latest_battery_v(v: f32) {
    if v > 0.0 {
        LATEST_BATTERY_V.store(v.to_bits(), core::sync::atomic::Ordering::Relaxed);
    }
}

static ATTACHED: AtomicU8 = AtomicU8::new(0);

pub fn attached_mask() -> u8 {
    ATTACHED.load(Ordering::Relaxed)
}

fn sync_attached(board: &SensorBoard) {
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
    sync_attached_mask(mask);
}

pub(crate) fn sync_attached_mask(mask: u8) {
    let prev = ATTACHED.load(Ordering::Relaxed);
    ATTACHED.store(mask, Ordering::Relaxed);
    if prev != mask {
        link::request_hello();
    }
}

/// Timestamped reading for `age_us` in the wire batch.
#[derive(Clone)]
pub struct StampedReading {
    pub reading: Reading,
    pub captured_us: u64,
}

#[derive(Clone)]
pub struct LatestSamples {
    /// Static readings queued since the last uplink (FIFO drain may add several).
    pub pending_static: Vec<StampedReading, MAX_BUFFERED_READINGS>,
    /// Mag readings queued since the last uplink (multiple poll reads per tick).
    pub pending_mag: Vec<StampedReading, MAX_BUFFERED_READINGS>,
    /// Airspeed readings queued since the last uplink (poll budget may add several).
    pub pending_airspeed: Vec<StampedReading, MAX_BUFFERED_READINGS>,
    /// Battery readings queued since the last uplink.
    pub pending_battery: Vec<StampedReading, MAX_BUFFERED_READINGS>,
}

fn age_us(pod_uptime_us: u64, captured_us: u64) -> u32 {
    pod_uptime_us
        .saturating_sub(captured_us)
        .min(u32::MAX as u64) as u32
}

impl LatestSamples {
    fn enqueue_static(&mut self, stamped: StampedReading) {
        if self.pending_static.len() >= MAX_BUFFERED_READINGS {
            let _ = self.pending_static.remove(0);
            note_dropped_reading("static");
        }
        let _ = self.pending_static.push(stamped);
    }

    fn enqueue_mag(&mut self, stamped: StampedReading) {
        if self.pending_mag.len() >= MAX_BUFFERED_READINGS {
            let _ = self.pending_mag.remove(0);
            note_dropped_reading("mag");
        }
        let _ = self.pending_mag.push(stamped);
    }

    fn enqueue_airspeed(&mut self, stamped: StampedReading) {
        if self.pending_airspeed.len() >= MAX_BUFFERED_READINGS {
            let _ = self.pending_airspeed.remove(0);
            note_dropped_reading("airspeed");
        }
        let _ = self.pending_airspeed.push(stamped);
    }

    fn enqueue_battery(&mut self, stamped: StampedReading) {
        if self.pending_battery.len() >= MAX_BUFFERED_READINGS {
            let _ = self.pending_battery.remove(0);
            note_dropped_reading("battery");
        }
        let _ = self.pending_battery.push(stamped);
    }
}

fn push_to_batch(samples: &mut Vec<Reading, MAX_READINGS>, reading: Reading) -> bool {
    samples.push(reading).is_ok()
}

fn drain_one_static(
    samples: &mut Vec<Reading, MAX_READINGS>,
    pending: &mut Vec<StampedReading, MAX_BUFFERED_READINGS>,
    pod_uptime_us: u64,
) -> bool {
    if pending.is_empty() || samples.len() >= MAX_READINGS {
        return false;
    }
    let s = pending[0].clone();
    let age = age_us(pod_uptime_us, s.captured_us);
    let wire = match s.reading {
        Reading::Static { p_pa, temp_c, .. } => Reading::Static {
            p_pa,
            temp_c,
            age_us: age,
        },
        _ => {
            let _ = pending.remove(0);
            return false;
        }
    };
    if push_to_batch(samples, wire) {
        let _ = pending.remove(0);
        true
    } else {
        false
    }
}

fn drain_one_mag(
    samples: &mut Vec<Reading, MAX_READINGS>,
    pending: &mut Vec<StampedReading, MAX_BUFFERED_READINGS>,
    pod_uptime_us: u64,
) -> bool {
    if pending.is_empty() || samples.len() >= MAX_READINGS {
        return false;
    }
    let s = pending[0].clone();
    let age = age_us(pod_uptime_us, s.captured_us);
    let wire = match s.reading {
        Reading::Mag {
            x_ut, y_ut, z_ut, ..
        } => Reading::Mag {
            x_ut,
            y_ut,
            z_ut,
            age_us: age,
        },
        _ => {
            let _ = pending.remove(0);
            return false;
        }
    };
    if push_to_batch(samples, wire) {
        let _ = pending.remove(0);
        true
    } else {
        false
    }
}

fn drain_one_airspeed(
    samples: &mut Vec<Reading, MAX_READINGS>,
    pending: &mut Vec<StampedReading, MAX_BUFFERED_READINGS>,
    pod_uptime_us: u64,
) -> bool {
    if pending.is_empty() || samples.len() >= MAX_READINGS {
        return false;
    }
    let s = pending[0].clone();
    let age = age_us(pod_uptime_us, s.captured_us);
    let wire = match s.reading {
        Reading::Airspeed { dp_pa, temp_c, .. } => Reading::Airspeed {
            dp_pa,
            temp_c,
            age_us: age,
        },
        _ => {
            let _ = pending.remove(0);
            return false;
        }
    };
    if push_to_batch(samples, wire) {
        let _ = pending.remove(0);
        true
    } else {
        false
    }
}

fn drain_one_battery(
    samples: &mut Vec<Reading, MAX_READINGS>,
    pending: &mut Vec<StampedReading, MAX_BUFFERED_READINGS>,
    pod_uptime_us: u64,
) -> bool {
    if pending.is_empty() || samples.len() >= MAX_READINGS {
        return false;
    }
    let s = pending[0].clone();
    let age = age_us(pod_uptime_us, s.captured_us);
    let wire = match s.reading {
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
        } => Reading::Battery {
            voltage_v,
            current_a,
            power_w,
            capacity_remain_mah,
            capacity_full_mah,
            soc_pct,
            time_remain_s,
            design_capacity_mah,
            age_us: age,
        },
        _ => {
            let _ = pending.remove(0);
            return false;
        }
    };
    if push_to_batch(samples, wire) {
        let _ = pending.remove(0);
        true
    } else {
        false
    }
}

impl LatestSamples {
    /// Build one wire batch (max [`MAX_READINGS`]). Round-robins sensor queues so
    /// BMP581 FIFO backlog cannot starve mag/airspeed/battery.
    pub fn build_batch(&mut self, pod_uptime_us: u64, seq: u32) -> SampleBatch {
        let mut samples: Vec<Reading, MAX_READINGS> = Vec::new();
        while samples.len() < MAX_READINGS {
            let before = samples.len();
            let _ = drain_one_static(&mut samples, &mut self.pending_static, pod_uptime_us);
            if samples.len() >= MAX_READINGS {
                break;
            }
            let _ = drain_one_mag(&mut samples, &mut self.pending_mag, pod_uptime_us);
            if samples.len() >= MAX_READINGS {
                break;
            }
            let _ = drain_one_airspeed(&mut samples, &mut self.pending_airspeed, pod_uptime_us);
            if samples.len() >= MAX_READINGS {
                break;
            }
            let _ = drain_one_battery(&mut samples, &mut self.pending_battery, pod_uptime_us);
            if samples.len() == before {
                break;
            }
        }
        SampleBatch {
            pod_uptime_us,
            seq,
            samples,
        }
    }
}

const EMPTY_SAMPLES: LatestSamples = LatestSamples {
    pending_static: Vec::new(),
    pending_mag: Vec::new(),
    pending_airspeed: Vec::new(),
    pending_battery: Vec::new(),
};
static SAMPLES: Mutex<RefCell<LatestSamples>> = Mutex::new(RefCell::new(EMPTY_SAMPLES));

pub fn with_samples_mut<R>(f: impl FnOnce(&mut LatestSamples) -> R) -> R {
    critical_section::with(|cs| f(&mut SAMPLES.borrow(cs).borrow_mut()))
}

// In burst-collect the radio is off: readings divert to the burst store and
// come back out as normal wire batches during the next uplink window.

pub fn push_static(reading: Reading, captured_us: u64) {
    if crate::power::defer_readings() {
        crate::burst::push(&reading, captured_us);
        return;
    }
    critical_section::with(|cs| {
        SAMPLES
            .borrow(cs)
            .borrow_mut()
            .enqueue_static(StampedReading {
                reading,
                captured_us,
            });
    });
}

pub fn push_mag(reading: Reading, captured_us: u64) {
    if crate::power::defer_readings() {
        crate::burst::push(&reading, captured_us);
        return;
    }
    critical_section::with(|cs| {
        SAMPLES.borrow(cs).borrow_mut().enqueue_mag(StampedReading {
            reading,
            captured_us,
        });
    });
}

pub fn push_airspeed(reading: Reading, captured_us: u64) {
    if crate::power::defer_readings() {
        crate::burst::push(&reading, captured_us);
        return;
    }
    critical_section::with(|cs| {
        SAMPLES
            .borrow(cs)
            .borrow_mut()
            .enqueue_airspeed(StampedReading {
                reading,
                captured_us,
            });
    });
}

pub fn push_battery(reading: Reading, captured_us: u64) {
    if crate::power::defer_readings() {
        crate::burst::push(&reading, captured_us);
        return;
    }
    critical_section::with(|cs| {
        SAMPLES
            .borrow(cs)
            .borrow_mut()
            .enqueue_battery(StampedReading {
                reading,
                captured_us,
            });
    });
}

/// Handles for sensors that probed and inited successfully (all optional).
pub struct SensorBoard {
    pub bmp581: Option<Bmp581>,
    pub mmc5983: Option<Mmc5983>,
    pub ms4525: Option<Ms4525>,
    pub bq27441: Option<Bq27441>,
}

fn try_attach_bmp581(bus: &mut Bus) -> Option<Bmp581> {
    let mut bmp581 = Bmp581::probe(bus)?;
    if bmp581.init(bus).is_ok() {
        println!("pod: bmp581 attached at 0x{:02x}", bmp581.addr());
        Some(bmp581)
    } else {
        println!("pod: bmp581 init failed at 0x{:02x}", bmp581.addr());
        None
    }
}

fn try_attach_mmc5983(bus: &mut Bus) -> Option<Mmc5983> {
    let mut mmc5983 = Mmc5983::probe(bus)?;
    if mmc5983.init(bus).is_ok() {
        println!("pod: mmc5983 attached at 0x{:02x}", mmc5983::ADDR);
        Some(mmc5983)
    } else {
        println!("pod: mmc5983 init failed");
        None
    }
}

fn try_attach_ms4525(bus: &mut Bus) -> Option<Ms4525> {
    let ms4525 = Ms4525::probe(bus)?;
    if ms4525.init(bus).is_ok() {
        println!("pod: ms4525 attached at 0x{:02x}", ms4525::ADDR);
        Some(ms4525)
    } else {
        None
    }
}

fn try_attach_bq27441(bus: &mut Bus) -> Option<Bq27441> {
    let mut bq27441 = Bq27441::probe(bus)?;
    if bq27441.init(bus).is_ok() {
        println!("pod: bq27441 attached at 0x{:02x}", bq27441::ADDR);
        Some(bq27441)
    } else {
        println!("pod: bq27441 init failed");
        None
    }
}

fn log_board_ready(board: &SensorBoard) {
    print!("pod: sensor board");
    if let Some(b) = &board.bmp581 {
        print!(" bmp581=0x{:02x}", b.addr());
    }
    if board.mmc5983.is_some() {
        print!(" mmc5983=0x{:02x}", mmc5983::ADDR);
    }
    if board.ms4525.is_some() {
        print!(" ms4525=0x{:02x}", ms4525::ADDR);
    }
    if board.bq27441.is_some() {
        print!(" bq27441=0x{:02x}", bq27441::ADDR);
    }
    if board.bmp581.is_none()
        && board.mmc5983.is_none()
        && board.ms4525.is_none()
        && board.bq27441.is_none()
    {
        println!(" (no sensors — will retry attach)");
    } else {
        println!();
    }
}

fn try_attach_missing(bus: &mut Bus, board: &mut SensorBoard) {
    if board.bmp581.is_none() {
        board.bmp581 = try_attach_bmp581(bus);
    }
    if board.mmc5983.is_none() {
        board.mmc5983 = try_attach_mmc5983(bus);
    }
    if board.ms4525.is_none() {
        board.ms4525 = try_attach_ms4525(bus);
    }
    if board.bq27441.is_none() {
        board.bq27441 = try_attach_bq27441(bus);
    }
}

/// Scan the bus and attach any sensors that respond. None are required.
pub fn bringup_board(bus: &mut Bus) -> SensorBoard {
    bus::scan(bus);
    let board = SensorBoard {
        bmp581: try_attach_bmp581(bus),
        mmc5983: try_attach_mmc5983(bus),
        ms4525: try_attach_ms4525(bus),
        bq27441: try_attach_bq27441(bus),
    };
    log_board_ready(&board);
    sync_attached(&board);
    board
}

/// Base-tick poll loop; re-probes missing sensors every 5 s. Does not return.
pub async fn run_sensor_poll(bus: &mut Bus, mut board: SensorBoard) {
    use pod_wire::SensorId;

    let mut ticker =
        embassy_time::Ticker::every(embassy_time::Duration::from_millis(crate::cfg::TICK_MS));
    let mut attach_ticks: u32 = 0;
    const ATTACH_INTERVAL: u32 = 50; // 5 s at 10 Hz

    let mut need_recovery = false;

    loop {
        ticker.next().await;
        let tick_start = Instant::now();
        let tick_us = tick_start.as_micros();
        let mut tick_failures = 0u8;

        // Always sample the gauge for sleep/wake policy, even when quiesced.
        if let Some(ref mut bq27441) = board.bq27441 {
            let hz = rates::get(SensorId::Battery);
            if hz > 0 {
                let cap_us = Instant::now().as_micros();
                match bq27441.read_when_due(bus) {
                    Ok(Some(s)) => {
                        store_latest_battery_v(s.voltage_v);
                        // Feed the live DesignCapacity (0x3C) readback to the
                        // config tracker; it confirms/clears any pending write.
                        crate::battery_cfg::note_chip(s.design_capacity_mah);
                        power::note_battery_sample(
                            cap_us,
                            s.voltage_v,
                            s.current_a,
                            s.soc_pct,
                            crate::battery_cfg::gauge_trusted(),
                        );
                        rates::note_read_ok(SensorId::Battery);
                        push_battery(
                            Reading::Battery {
                                voltage_v: s.voltage_v,
                                current_a: s.current_a,
                                power_w: s.power_w,
                                capacity_remain_mah: s.capacity_remain_mah,
                                capacity_full_mah: s.capacity_full_mah,
                                soc_pct: s.soc_pct,
                                time_remain_s: s.time_remain_s,
                                design_capacity_mah: s.design_capacity_mah,
                                age_us: 0,
                            },
                            cap_us,
                        );
                    }
                    Ok(None) => {}
                    Err(()) => {
                        tick_failures = tick_failures.saturating_add(1);
                        if rates::note_read_fail(SensorId::Battery) {
                            need_recovery = true;
                        }
                    }
                }
            }
            // Config update needs a quiet bus; run even when quiesced for sleep.
            if let Some(mah) = crate::battery_cfg::should_program_design(tick_us) {
                match bq27441.program_design_capacity(bus, mah) {
                    Ok(()) => {
                        println!("pod: bq27441 design capacity programmed {mah} mAh");
                        crate::battery_cfg::note_program_ok(mah);
                    }
                    Err(()) => {
                        crate::battery_cfg::note_program_fail(tick_us);
                        if crate::battery_cfg::should_log_program_fail() {
                            println!("pod: bq27441 design capacity program failed; retry in 60s");
                        }
                    }
                }
            }
        }

        rates::begin_tick();
        // Wall time intentionally spent awaiting between spread mag polls;
        // excluded from the tick-overrun measurement below.
        let mut slept_us: u64 = 0;

        if board.ms4525.is_some() {
            let hz = rates::get(SensorId::Airspeed);
            for _ in 0..rates::poll_budget(SensorId::Airspeed, hz) {
                if let Some(ref ms4525) = board.ms4525 {
                    let cap_us = Instant::now().as_micros();
                    match ms4525.read(bus) {
                        Ok(s) => {
                            rates::note_read_ok(SensorId::Airspeed);
                            push_airspeed(
                                Reading::Airspeed {
                                    dp_pa: s.dp_pa,
                                    temp_c: s.temp_c,
                                    age_us: 0,
                                },
                                cap_us,
                            );
                        }
                        Err(()) => {
                            tick_failures = tick_failures.saturating_add(1);
                            if rates::note_read_fail(SensorId::Airspeed) {
                                need_recovery = true;
                            }
                        }
                    }
                }
            }
        }
        if let Some(ref mut bmp581) = board.bmp581 {
            let hz = rates::get(SensorId::Static);
            if hz > 0 {
                if bmp581.ensure_odr(bus, hz).is_err() {
                    tick_failures = tick_failures.saturating_add(1);
                    if rates::note_read_fail(SensorId::Static) {
                        need_recovery = true;
                    }
                } else {
                    // Stamp with the actual drain moment, not tick start: the
                    // mag spread loop can shift this call within the tick.
                    let drain_us = Instant::now().as_micros();
                    match bmp581.drain_fifo(bus, drain_us) {
                        Ok(frames) => {
                            if !frames.is_empty() {
                                rates::note_read_ok(SensorId::Static);
                            }
                            for (s, cap_us) in frames {
                                push_static(
                                    Reading::Static {
                                        p_pa: s.p_pa,
                                        temp_c: s.temp_c,
                                        age_us: 0,
                                    },
                                    cap_us,
                                );
                            }
                        }
                        Err(()) => {
                            tick_failures = tick_failures.saturating_add(1);
                            if rates::note_read_fail(SensorId::Static) {
                                need_recovery = true;
                            }
                        }
                    }
                }
            }
        }

        // Mag last: the MMC5983 has no FIFO, so its status bit only ever holds
        // one sample. Harvesting `budget` samples per tick means polling once
        // per sample period, awaiting in between — the bus is idle while we
        // sleep, but the tick's other sensors have already run.
        if let Some(ref mut mmc5983) = board.mmc5983 {
            let hz = rates::get(SensorId::Mag);
            if hz > 0 {
                if mmc5983.ensure_cm_freq(bus, hz).is_err() {
                    tick_failures = tick_failures.saturating_add(1);
                    if rates::note_read_fail(SensorId::Mag) {
                        need_recovery = true;
                    }
                } else {
                    let budget = rates::poll_budget(SensorId::Mag, hz);
                    let spread_us = (1_000_000u64 / hz as u64).min(20_000);
                    // A poll can land just before the sample completes; one
                    // short nudge-and-retry recovers it and re-phases the loop.
                    const MISS_NUDGE_US: u64 = 4_000;
                    let mut pushed = 0u32;
                    for i in 0..budget {
                        if i > 0 {
                            embassy_time::Timer::after(
                                embassy_time::Duration::from_micros(spread_us),
                            )
                            .await;
                            slept_us += spread_us;
                        }
                        let mut retried = false;
                        loop {
                            let cap_us = Instant::now().as_micros();
                            match mmc5983.read_when_ready(bus) {
                                Ok(Some(s)) => {
                                    rates::note_read_ok(SensorId::Mag);
                                    pushed += 1;
                                    push_mag(
                                        Reading::Mag {
                                            x_ut: s.x_ut,
                                            y_ut: s.y_ut,
                                            z_ut: s.z_ut,
                                            age_us: 0,
                                        },
                                        cap_us,
                                    );
                                }
                                Ok(None) if !retried => {
                                    retried = true;
                                    embassy_time::Timer::after(
                                        embassy_time::Duration::from_micros(MISS_NUDGE_US),
                                    )
                                    .await;
                                    slept_us += MISS_NUDGE_US;
                                    continue;
                                }
                                Ok(None) => {}
                                Err(()) => {
                                    tick_failures = tick_failures.saturating_add(1);
                                    if rates::note_read_fail(SensorId::Mag) {
                                        need_recovery = true;
                                    }
                                }
                            }
                            break;
                        }
                    }
                    // If status never flagged ready (e.g. first tick after ODR change),
                    // still take one direct read so mag does not go dark.
                    if pushed == 0 && budget > 0 {
                        let cap_us = Instant::now().as_micros();
                        if let Ok(s) = mmc5983.read_sample(bus) {
                            rates::note_read_ok(SensorId::Mag);
                            push_mag(
                                Reading::Mag {
                                    x_ut: s.x_ut,
                                    y_ut: s.y_ut,
                                    z_ut: s.z_ut,
                                    age_us: 0,
                                },
                                cap_us,
                            );
                        }
                    }
                }
            }
        }

        // Overrun accounting excludes the intentional mag-spread sleeps.
        let busy_ms = tick_start
            .elapsed()
            .as_millis()
            .saturating_sub(slept_us / 1000);
        if busy_ms > 80 {
            if rates::note_tick_overrun() {
                need_recovery = true;
            }
        } else {
            rates::clear_overrun_streak();
        }
        if tick_failures >= 8 {
            need_recovery = true;
        }
        if need_recovery {
            recover::recover_bus(bus, &mut board);
            need_recovery = false;
        }

        if board.bmp581.is_none()
            || board.mmc5983.is_none()
            || board.ms4525.is_none()
            || board.bq27441.is_none()
        {
            attach_ticks = attach_ticks.saturating_add(1);
            if attach_ticks >= ATTACH_INTERVAL {
                attach_ticks = 0;
                try_attach_missing(bus, &mut board);
                sync_attached(&board);
            }
        }
    }
}
