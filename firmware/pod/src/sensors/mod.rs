//! Sensor drivers and latest-sample state shared with the uplink task.

pub mod bmp581;
pub mod bus;

use core::cell::RefCell;

use critical_section::Mutex;
use embassy_time::Instant;
use heapless::Vec;
use pod_wire::{Reading, SampleBatch, MAX_READINGS};

use bmp581::Bmp581;
use bus::Bus;
use esp_println::println;

/// Timestamped reading for `age_us` in the wire batch.
#[derive(Clone)]
pub struct StampedReading {
    pub reading: Reading,
    pub captured_us: u64,
}

#[derive(Clone)]
pub struct LatestSamples {
    pub static_sample: Option<StampedReading>,
}

impl Default for LatestSamples {
    fn default() -> Self {
        Self { static_sample: None }
    }
}

impl LatestSamples {
    pub fn build_batch(&self, pod_uptime_us: u64, seq: u32) -> SampleBatch {
        let mut samples: Vec<Reading, MAX_READINGS> = Vec::new();
        if let Some(s) = &self.static_sample {
            let age_us = pod_uptime_us.saturating_sub(s.captured_us);
            let Reading::Static { p_pa, temp_c, .. } = s.reading else {
                // unreachable
                return SampleBatch {
                    pod_uptime_us,
                    seq,
                    samples,
                };
            };
            let _ = samples.push(Reading::Static {
                p_pa,
                temp_c,
                age_us: age_us.min(u32::MAX as u64) as u32,
            });
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

/// Scan, probe, and init BMP581. Returns the driver handle on success.
pub fn bringup_bmp581(bus: &mut Bus) -> Option<Bmp581> {
    bus::scan(bus);
    let bmp581 = Bmp581::probe(bus)?;
    if bmp581.init(bus).is_err() {
        println!("pod: bmp581 init failed at 0x{:02x}", bmp581.addr());
        return None;
    }
    println!("pod: sensor board ready at bmp581 0x{:02x}", bmp581.addr());
    Some(bmp581)
}

/// 10 Hz poll loop; does not return.
pub async fn run_bmp581_poll(bus: &mut Bus, bmp581: Bmp581) {
    let mut ticker = embassy_time::Ticker::every(embassy_time::Duration::from_millis(
        crate::cfg::TICK_MS,
    ));
    loop {
        ticker.next().await;
        let captured_us = Instant::now().as_micros();
        match bmp581.read(bus) {
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
    }
}
