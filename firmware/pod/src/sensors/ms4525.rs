//! Honeywell MS4525DO differential pressure + temperature (I²C).
//!
//! Protocol and transfer function follow PX4 `ms4525_airspeed` and
//! cojmeister/ms4525do (±1 PSI, output type A). Bench part: DS5AI001DP @ 0x28.
//!
//! After the measure command the die needs ~1–2 ms before status is normal; reading
//! immediately yields status busy/stale and looks like an I²C failure in the poll loop.

use embassy_time::{block_for, Duration};
use esp_println::{print, println};

use super::bus::Bus as I2cBus;

/// Wait after triggering a conversion (datasheet / PX4-style drivers use ~2 ms).
const CONV_WAIT_US: u64 = 2_000;
/// Extra wait between fetch retries when status is still busy.
const FETCH_RETRY_WAIT_US: u64 = 500;
const MAX_FETCH_TRIES: u8 = 4;

pub const ADDR: u8 = 0x28;

const CMD_MEASURE: u8 = 0x00;
const BRIDGE_MASK: u8 = 0x3f;
const TEMP_TOP_MASK: u8 = 0xe0;
const PSI_TO_PA: f32 = 6894.757;
const P_MIN_PSI: f32 = -1.0;
const P_MAX_PSI: f32 = 1.0;
const COUNTS_FULL: f32 = 16383.0;

/// Differential pressure [Pa] and die temperature [°C].
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sample {
    pub dp_pa: f32,
    pub temp_c: f32,
}

pub struct Ms4525;

impl Ms4525 {
    pub fn probe(bus: &mut I2cBus) -> Option<Self> {
        match read_and_decode(bus) {
            Ok(_) => {
                println!("pod: ms4525 found at 0x{ADDR:02x}");
                Some(Self)
            }
            Err(e) => {
                println!("pod: ms4525 probe at 0x{ADDR:02x}: {e}");
                None
            }
        }
    }

    pub fn init(&self, _bus: &mut I2cBus) -> Result<(), ()> {
        println!("pod: ms4525 init ok");
        Ok(())
    }

    pub fn read(&self, bus: &mut I2cBus) -> Result<Sample, ()> {
        read_and_decode(bus).map_err(|_| ())
    }

    /// Log a single measure attempt for [`bus::scan`].
    pub fn scan_line(bus: &mut I2cBus) -> u8 {
        match fetch_raw(bus) {
            Ok(data) => {
                let st = status_from_byte(data[0]);
                print!(" 0x{ADDR:02x}(st={st})");
                1
            }
            Err(e) => {
                print!(" 0x{ADDR:02x}(err={e})");
                0
            }
        }
    }
}

fn read_and_decode(bus: &mut I2cBus) -> Result<Sample, &'static str> {
    trigger_conversion(bus)?;
    block_for(Duration::from_micros(CONV_WAIT_US));
    fetch_and_decode(bus)
}

fn trigger_conversion(bus: &mut I2cBus) -> Result<(), &'static str> {
    bus.write(ADDR, &[CMD_MEASURE]).map_err(|_| "trigger")
}

/// Read four data bytes without sending a new measure command.
fn fetch_raw(bus: &mut I2cBus) -> Result<[u8; 4], &'static str> {
    let mut data = [0u8; 4];
    bus.write_read(ADDR, &[], &mut data)
        .map_err(|_| "read")?;
    Ok(data)
}

fn fetch_and_decode(bus: &mut I2cBus) -> Result<Sample, &'static str> {
    let mut last = "status not ready";
    for attempt in 0..MAX_FETCH_TRIES {
        let data = fetch_raw(bus)?;
        match decode(&data) {
            Ok(sample) => return Ok(sample),
            Err(e) => {
                last = e;
                if (e == "status not ready" || e == "fault") && attempt + 1 < MAX_FETCH_TRIES {
                    block_for(Duration::from_micros(FETCH_RETRY_WAIT_US));
                    continue;
                }
                return Err(e);
            }
        }
    }
    Err(last)
}

fn status_from_byte(b0: u8) -> u8 {
    (b0 >> 6) & 0x03
}

fn decode(data: &[u8; 4]) -> Result<Sample, &'static str> {
    match status_from_byte(data[0]) {
        0 => {}
        1 | 2 => return Err("status not ready"),
        3 => return Err("fault"),
        _ => return Err("status"),
    }

    let bridge = ((data[0] & BRIDGE_MASK) as u16) << 8 | data[1] as u16;
    let temp_cnts = (((data[2] as u16) << 8) | ((data[3] & TEMP_TOP_MASK) as u16)) >> 5;
    if temp_cnts == 2047 {
        return Err("temp invalid");
    }

    Ok(Sample {
        dp_pa: bridge_to_dp_pa(bridge),
        temp_c: temp_counts_to_c(temp_cnts),
    })
}

fn bridge_to_dp_pa(bridge: u16) -> f32 {
    let raw = bridge as f32;
    let dp_psi = -((raw - 0.1 * COUNTS_FULL) * (P_MAX_PSI - P_MIN_PSI) / (0.8 * COUNTS_FULL) + P_MIN_PSI);
    dp_psi * PSI_TO_PA
}

fn temp_counts_to_c(counts: u16) -> f32 {
    (200.0 * counts as f32 / 2047.0) - 50.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bridge_midscale_near_zero_pa() {
        let pa = bridge_to_dp_pa(8192);
        assert!(pa.abs() < 500.0, "midscale pa={pa}");
    }

    #[test]
    fn temp_counts_endpoints() {
        assert!((temp_counts_to_c(0) - (-50.0)).abs() < 0.1);
        assert!((temp_counts_to_c(2047) - 150.0).abs() < 0.1);
    }

    #[test]
    fn decode_normal_packet() {
        // status=0 in top bits; mid-scale pressure; valid temp
        let data = [0x20, 0x00, 0x80, 0x00];
        let s = decode(&data).unwrap();
        assert!(s.temp_c > -50.0 && s.temp_c < 150.0);
    }
}
