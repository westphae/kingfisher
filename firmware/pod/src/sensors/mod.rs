//! Sensor drivers and latest-sample state shared with the uplink task.

pub mod bmp581;
pub mod bus;
pub mod mmc5983;

use core::cell::RefCell;

use critical_section::Mutex;
use embassy_time::Instant;
use heapless::Vec;
use pod_wire::{Reading, SampleBatch, MAX_READINGS};

use bmp581::Bmp581;
use bus::Bus;
use esp_println::println;
use mmc5983::Mmc5983;

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

pub struct SensorBoard {
    pub bmp581: Bmp581,
    pub mmc5983: Mmc5983,
}

/// Scan, probe, and init BMP581 + MMC5983. Returns board handles on success.
pub fn bringup_board(bus: &mut Bus) -> Option<SensorBoard> {
    bus::scan(bus);
    let bmp581 = Bmp581::probe(bus)?;
    if bmp581.init(bus).is_err() {
        println!("pod: bmp581 init failed at 0x{:02x}", bmp581.addr());
        return None;
    }
    let mmc5983 = Mmc5983::probe(bus)?;
    if mmc5983.init(bus).is_err() {
        println!("pod: mmc5983 init failed");
        return None;
    }
    println!(
        "pod: sensor board ready bmp581=0x{:02x} mmc5983=0x{:02x}",
        bmp581.addr(),
        mmc5983::ADDR
    );
    Some(SensorBoard { bmp581, mmc5983 })
}

/// 10 Hz poll loop for all sensors; does not return.
pub async fn run_sensor_poll(bus: &mut Bus, board: SensorBoard) {
    let mut ticker = embassy_time::Ticker::every(embassy_time::Duration::from_millis(
        crate::cfg::TICK_MS,
    ));
    loop {
        ticker.next().await;
        let captured_us = Instant::now().as_micros();
        match board.bmp581.read(bus) {
            Ok(s) => {
                update_static(
                    Reading::Static {
                        p_pa: s.p_pa,
                        temp_c: s.temp_c,
                        age_us: 0,
                    },
                    captured_us,
                );
            }
            Err(()) => println!("pod: bmp581 read failed"),
        }
        match board.mmc5983.read(bus) {
            Ok(s) => {
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
            Err(()) => println!("pod: mmc5983 read failed"),
        }
    }
}
