//! Sensor drivers and latest-sample state shared with the uplink task.

pub mod bmp581;
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
use bus::Bus;
use esp_println::{print, println};
use mmc5983::Mmc5983;
use ms4525::Ms4525;

use crate::link;
use crate::rates;

pub const BMP_BIT: u8 = 1;
pub const MMC_BIT: u8 = 2;
pub const MS4525_BIT: u8 = 4;

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

#[derive(Clone, Default)]
pub struct LatestSamples {
    pub static_sample: Option<StampedReading>,
    pub mag_sample: Option<StampedReading>,
    pub airspeed_sample: Option<StampedReading>,
}

impl LatestSamples {
    pub fn build_batch(&self, pod_uptime_us: u64, seq: u32) -> SampleBatch {
        let mut samples: Vec<Reading, MAX_READINGS> = Vec::new();
        if let Some(s) = &self.static_sample {
            let age_us = pod_uptime_us.saturating_sub(s.captured_us);
            if let Reading::Static { p_pa, temp_c, .. } = s.reading {
                let _ = samples.push(Reading::Static {
                    p_pa,
                    temp_c,
                    age_us: age_us.min(u32::MAX as u64) as u32,
                });
            }
        }
        if let Some(s) = &self.mag_sample {
            let age_us = pod_uptime_us.saturating_sub(s.captured_us);
            if let Reading::Mag {
                x_ut,
                y_ut,
                z_ut,
                ..
            } = s.reading
            {
                let _ = samples.push(Reading::Mag {
                    x_ut,
                    y_ut,
                    z_ut,
                    age_us: age_us.min(u32::MAX as u64) as u32,
                });
            }
        }
        if let Some(s) = &self.airspeed_sample {
            let age_us = pod_uptime_us.saturating_sub(s.captured_us);
            if let Reading::Airspeed { dp_pa, temp_c, .. } = s.reading {
                let _ = samples.push(Reading::Airspeed {
                    dp_pa,
                    temp_c,
                    age_us: age_us.min(u32::MAX as u64) as u32,
                });
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
    static_sample: None,
    mag_sample: None,
    airspeed_sample: None,
};
static SAMPLES: Mutex<RefCell<LatestSamples>> = Mutex::new(RefCell::new(EMPTY_SAMPLES));

pub fn with_samples<R>(f: impl FnOnce(&LatestSamples) -> R) -> R {
    critical_section::with(|cs| f(&SAMPLES.borrow(cs).borrow()))
}

pub fn update_static(reading: Reading, captured_us: u64) {
    critical_section::with(|cs| {
        SAMPLES
            .borrow(cs)
            .borrow_mut()
            .static_sample = Some(StampedReading {
            reading,
            captured_us,
        });
    });
}

pub fn update_mag(reading: Reading, captured_us: u64) {
    critical_section::with(|cs| {
        SAMPLES
            .borrow(cs)
            .borrow_mut()
            .mag_sample = Some(StampedReading {
            reading,
            captured_us,
        });
    });
}

pub fn update_airspeed(reading: Reading, captured_us: u64) {
    critical_section::with(|cs| {
        SAMPLES
            .borrow(cs)
            .borrow_mut()
            .airspeed_sample = Some(StampedReading {
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
}

fn try_attach_bmp581(bus: &mut Bus) -> Option<Bmp581> {
    let bmp581 = Bmp581::probe(bus)?;
    if bmp581.init(bus).is_ok() {
        println!("pod: bmp581 attached at 0x{:02x}", bmp581.addr());
        Some(bmp581)
    } else {
        println!("pod: bmp581 init failed at 0x{:02x}", bmp581.addr());
        None
    }
}

fn try_attach_mmc5983(bus: &mut Bus) -> Option<Mmc5983> {
    let mmc5983 = Mmc5983::probe(bus)?;
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
    if board.bmp581.is_none() && board.mmc5983.is_none() && board.ms4525.is_none() {
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
}

/// Scan the bus and attach any sensors that respond. None are required.
pub fn bringup_board(bus: &mut Bus) -> SensorBoard {
    bus::scan(bus);
    let board = SensorBoard {
        bmp581: try_attach_bmp581(bus),
        mmc5983: try_attach_mmc5983(bus),
        ms4525: try_attach_ms4525(bus),
    };
    log_board_ready(&board);
    sync_attached(&board);
    board
}

/// Base-tick poll loop; re-probes missing sensors every 5 s. Does not return.
pub async fn run_sensor_poll(bus: &mut Bus, mut board: SensorBoard) {
    use pod_wire::SensorId;

    let mut ticker = embassy_time::Ticker::every(embassy_time::Duration::from_millis(
        crate::cfg::TICK_MS,
    ));
    let mut attach_ticks: u32 = 0;
    const ATTACH_INTERVAL: u32 = 50; // 5 s at 10 Hz

    let mut need_recovery = false;

    loop {
        ticker.next().await;
        let tick_start = Instant::now();
        rates::begin_tick();
        let captured_us = tick_start.as_micros();
        let mut tick_failures = 0u8;

        // Cheap sensors first so mag/airspeed keep updating under BMP load.
        if board.mmc5983.is_some() {
            let hz = rates::get(SensorId::Mag);
            for _ in 0..rates::poll_budget(SensorId::Mag, hz) {
                if let Some(ref mmc5983) = board.mmc5983 {
                    match mmc5983.read(bus) {
                        Ok(s) => {
                            rates::note_read_ok(SensorId::Mag);
                            update_mag(
                                Reading::Mag {
                                    x_ut: s.x_ut,
                                    y_ut: s.y_ut,
                                    z_ut: s.z_ut,
                                    age_us: 0,
                                },
                                captured_us,
                            );
                        }
                        Err(()) => {
                            tick_failures = tick_failures.saturating_add(1);
                            if rates::note_read_fail(SensorId::Mag) {
                                need_recovery = true;
                            }
                        }
                    }
                }
            }
        }
        if board.ms4525.is_some() {
            let hz = rates::get(SensorId::Airspeed);
            for _ in 0..rates::poll_budget(SensorId::Airspeed, hz) {
                if let Some(ref ms4525) = board.ms4525 {
                    match ms4525.read(bus) {
                        Ok(s) => {
                            rates::note_read_ok(SensorId::Airspeed);
                            update_airspeed(
                                Reading::Airspeed {
                                    dp_pa: s.dp_pa,
                                    temp_c: s.temp_c,
                                    age_us: 0,
                                },
                                captured_us,
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
        if board.bmp581.is_some() {
            let hz = rates::get(SensorId::Static);
            for _ in 0..rates::poll_budget(SensorId::Static, hz) {
                if let Some(ref bmp581) = board.bmp581 {
                    match bmp581.read(bus) {
                        Ok(s) => {
                            rates::note_read_ok(SensorId::Static);
                            update_static(
                                Reading::Static {
                                    p_pa: s.p_pa,
                                    temp_c: s.temp_c,
                                    age_us: 0,
                                },
                                captured_us,
                            );
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

        let elapsed_ms = tick_start.elapsed().as_millis() as u64;
        if elapsed_ms > 80 {
            if rates::note_tick_overrun() {
                need_recovery = true;
            }
        } else {
            rates::clear_overrun_streak();
        }
        if tick_failures >= 4 {
            need_recovery = true;
        }
        if need_recovery {
            recover::recover_bus(bus, &mut board);
            need_recovery = false;
        }

        if board.bmp581.is_none() || board.mmc5983.is_none() || board.ms4525.is_none() {
            attach_ticks = attach_ticks.saturating_add(1);
            if attach_ticks >= ATTACH_INTERVAL {
                attach_ticks = 0;
                try_attach_missing(bus, &mut board);
                sync_attached(&board);
            }
        }
    }
}
