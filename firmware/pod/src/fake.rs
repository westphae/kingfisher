//! Phase 1 fake-data generator. Emits one Airspeed + Static + Mag
//! reading per tick, each slowly varying so the cockpit UI shows life
//! when Phase 2 ingests this stream.
//!
//! Real sensors replace this in Phase 3 — at which point this module is
//! deleted.

use heapless::Vec;
use libm::{cosf, sinf};
use pod_wire::{Reading, SampleBatch, MAX_READINGS};

pub struct Generator {
    seq: u32,
}

impl Generator {
    pub const fn new() -> Self {
        Self { seq: 0 }
    }

    pub fn next(&mut self, pod_uptime_us: u64) -> SampleBatch {
        self.seq = self.seq.wrapping_add(1);
        let t = (pod_uptime_us as f32) / 1_000_000.0;

        // ~10 s periods so traces are easy to eyeball.
        let dp = 50.0 + 10.0 * sinf(t * 0.628);
        let p = 98_000.0 + 50.0 * sinf(t * 0.314);
        let mag_phase = t * 1.256;
        let mag_x = 25.0 * cosf(mag_phase);
        let mag_y = 25.0 * sinf(mag_phase);
        let mag_z = 40.0;
        let temp = 18.0 + 0.5 * sinf(t * 0.1);

        let mut samples: Vec<Reading, MAX_READINGS> = Vec::new();
        let _ = samples.push(Reading::Airspeed {
            dp_pa: dp,
            temp_c: temp,
            age_us: 0,
        });
        let _ = samples.push(Reading::Static {
            p_pa: p,
            temp_c: temp,
            age_us: 0,
        });
        let _ = samples.push(Reading::Mag {
            x_ut: mag_x,
            y_ut: mag_y,
            z_ut: mag_z,
            age_us: 0,
        });

        SampleBatch {
            pod_uptime_us,
            seq: self.seq,
            samples,
        }
    }
}
