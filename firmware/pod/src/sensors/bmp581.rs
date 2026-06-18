//! Bosch BMP581 static pressure + temperature (I²C).
//!
//! NORMAL mode + on-chip FIFO (PT frames). The sensor samples at the
//! configured ODR; the poll loop drains all pending frames once per tick
//! and assigns capture times from ODR and frame order.

use esp_hal::delay::Delay;
use esp_println::println;
use heapless::Vec;
use pod_wire::MAX_READINGS;

use super::bus::Bus as I2cBus;

pub const ADDR_PRIMARY: u8 = 0x46;
pub const ADDR_SECONDARY: u8 = 0x47;

const REG_CHIP_ID: u8 = 0x01;
const REG_INT_SOURCE: u8 = 0x15;
const REG_FIFO_CONFIG: u8 = 0x16;
const REG_FIFO_COUNT: u8 = 0x17;
const REG_FIFO_SEL: u8 = 0x18;
const REG_FIFO_DATA: u8 = 0x29;
const REG_INT_STATUS: u8 = 0x27;
const REG_STATUS: u8 = 0x28;
const REG_OSR: u8 = 0x36;
const REG_ODR: u8 = 0x37;
const REG_CMD: u8 = 0x7E;

const CHIP_ID_BMP581: u8 = 0x50;
const CHIP_ID_BMP585: u8 = 0x51;
const CMD_SOFT_RESET: u8 = 0xB6;

const PWR_STANDBY: u8 = 0;
const PWR_NORMAL: u8 = 1;

const OSR_TEMP_NONE: u8 = 0;
const OSR_PRESS_NONE: u8 = 0;

/// FIFO frame: pressure + temperature (48 bit / 6 bytes).
const FIFO_FRAME_SEL_PT: u8 = 0x03;
const FIFO_FRAME_BYTES: usize = 6;
const FIFO_FRAME_EMPTY: u8 = 0x7F;
/// Max PT frames in FIFO (datasheet).
const FIFO_MAX_FRAMES_PT: u8 = 16;

/// Pressure [Pa] and temperature [°C].
#[derive(Debug, Clone, Copy)]
pub struct Sample {
    pub p_pa: f32,
    pub temp_c: f32,
}

pub struct Bmp581 {
    addr: u8,
    active_hz: u16,
}

impl Bmp581 {
    pub fn addr(&self) -> u8 {
        self.addr
    }

    pub fn probe(bus: &mut I2cBus) -> Option<Self> {
        for addr in [ADDR_SECONDARY, ADDR_PRIMARY] {
            match read_u8(bus, addr, REG_CHIP_ID) {
                Ok(id) => {
                    println!("pod: i2c 0x{addr:02x} chip_id=0x{id:02x}");
                    if id == CHIP_ID_BMP581 || id == CHIP_ID_BMP585 {
                        return Some(Self { addr, active_hz: 0 });
                    }
                }
                Err(e) => println!("pod: i2c 0x{addr:02x} chip_id read: {:?}", e),
            }
        }
        None
    }

    pub fn init(&mut self, bus: &mut I2cBus) -> Result<(), ()> {
        self.soft_reset(bus)?;
        let id = read_u8(bus, self.addr, REG_CHIP_ID).map_err(|_| ())?;
        if id != CHIP_ID_BMP581 && id != CHIP_ID_BMP585 {
            println!("pod: bmp581 bad chip_id 0x{id:02x}");
            return Err(());
        }
        self.wait_nvm_ready(bus)?;
        println!("pod: bmp581 init ok at 0x{:02x} (FIFO)", self.addr);
        Ok(())
    }

    /// Ensure NORMAL+FIFO is running at `hz` (reconfigures from standby when Hz changes).
    pub fn ensure_odr(&mut self, bus: &mut I2cBus, hz: u16) -> Result<(), ()> {
        let hz = hz.max(1);
        if self.active_hz == hz {
            return Ok(());
        }
        self.configure_fifo_normal(bus, hz)?;
        self.active_hz = hz;
        println!("pod: bmp581 ODR {hz} Hz");
        Ok(())
    }

    /// Drain all FIFO frames; oldest frame first. `now_us` is the drain time on
    /// the pod clock; capture times are reconstructed from `active_hz`.
    pub fn drain_fifo(
        &self,
        bus: &mut I2cBus,
        now_us: u64,
    ) -> Result<Vec<(Sample, u64), MAX_READINGS>, ()> {
        if self.active_hz == 0 {
            return Ok(Vec::new());
        }
        let count = read_u8(bus, self.addr, REG_FIFO_COUNT).map_err(|_| ())?;
        let n = (count & 0x1F).min(FIFO_MAX_FRAMES_PT) as usize;
        if n == 0 {
            return Ok(Vec::new());
        }

        let mut raw = [0u8; FIFO_MAX_FRAMES_PT as usize * FIFO_FRAME_BYTES];
        let len = n * FIFO_FRAME_BYTES;
        read_block(bus, self.addr, REG_FIFO_DATA, &mut raw[..len])?;

        let period_us = 1_000_000u64 / self.active_hz as u64;
        let mut out: Vec<(Sample, u64), MAX_READINGS> = Vec::new();
        for i in 0..n {
            let frame = &raw[i * FIFO_FRAME_BYTES..][..FIFO_FRAME_BYTES];
            if frame[0] == FIFO_FRAME_EMPTY {
                break;
            }
            let frame6: [u8; FIFO_FRAME_BYTES] = frame.try_into().map_err(|_| ())?;
            let sample = decode_pt_frame(&frame6)?;
            let age_frames = (n - 1 - i) as u64;
            let captured_us = now_us.saturating_sub(age_frames.saturating_mul(period_us));
            let _ = out.push((sample, captured_us));
        }
        Ok(out)
    }

    fn configure_fifo_normal(&self, bus: &mut I2cBus, hz: u16) -> Result<(), ()> {
        let odr = odr_code_for_hz(hz).ok_or(())?;
        self.enter_standby(bus)?;

        write_u8(bus, self.addr, REG_INT_SOURCE, 0x01)?; // drdy_data_reg_en

        // Field layout kept explicit (`<< 0` documents the temp-OSR bit offset).
        #[allow(clippy::identity_op)]
        let osr = (OSR_PRESS_NONE << 3) | (OSR_TEMP_NONE << 0) | (1 << 6); // press_en
        write_u8(bus, self.addr, REG_OSR, osr)?;
        // PT frames, no decimation, streaming FIFO, threshold off.
        write_u8(bus, self.addr, REG_FIFO_SEL, FIFO_FRAME_SEL_PT)?;
        write_u8(bus, self.addr, REG_FIFO_CONFIG, 0x00)?;
        let odr_val = (PWR_NORMAL & 0x03) | (odr << 2);
        write_u8(bus, self.addr, REG_ODR, odr_val)?;
        Delay::new().delay_millis(5);
        Ok(())
    }

    fn enter_standby(&self, bus: &mut I2cBus) -> Result<(), ()> {
        write_u8(bus, self.addr, REG_ODR, PWR_STANDBY)?;
        Delay::new().delay_millis(2);
        Ok(())
    }

    fn soft_reset(&mut self, bus: &mut I2cBus) -> Result<(), ()> {
        write_u8(bus, self.addr, REG_CMD, CMD_SOFT_RESET)?;
        Delay::new().delay_micros(3_000);
        let st = read_u8(bus, self.addr, REG_INT_STATUS).map_err(|_| ())?;
        if st & 0x10 == 0 {
            println!("pod: bmp581 reset: POR not set (int_status=0x{st:02x})");
            return Err(());
        }
        self.active_hz = 0;
        Ok(())
    }

    fn wait_nvm_ready(&self, bus: &mut I2cBus) -> Result<(), ()> {
        for _ in 0..100 {
            let st = read_u8(bus, self.addr, REG_STATUS).map_err(|_| ())?;
            if st & 0x02 != 0 && st & 0x04 == 0 {
                return Ok(());
            }
            Delay::new().delay_micros(1_000);
        }
        let st = read_u8(bus, self.addr, REG_STATUS).unwrap_or(0xff);
        println!("pod: bmp581 NVM not ready (status=0x{st:02x})");
        Err(())
    }
}

/// Map requested Hz to the highest supported NORMAL-mode ODR not above `hz`.
fn odr_code_for_hz(hz: u16) -> Option<u8> {
    const TABLE: &[(u16, u8)] = &[
        (240, 0x00),
        (218, 0x01),
        (199, 0x02),
        (179, 0x03),
        (160, 0x04),
        (149, 0x05),
        (140, 0x06),
        (129, 0x07),
        (120, 0x08),
        (110, 0x09),
        (100, 0x0A),
        (89, 0x0B),
        (80, 0x0C),
        (70, 0x0D),
        (60, 0x0E),
        (50, 0x0F),
        (45, 0x10),
        (40, 0x11),
        (35, 0x12),
        (30, 0x13),
        (25, 0x14),
        (20, 0x15),
        (15, 0x16),
        (10, 0x17),
        (5, 0x18),
        (4, 0x19),
        (3, 0x1A),
        (2, 0x1B),
        (1, 0x1C),
    ];
    for &(h, code) in TABLE {
        if h <= hz {
            return Some(code);
        }
    }
    Some(0x1C)
}

fn decode_pt_frame(data: &[u8; FIFO_FRAME_BYTES]) -> Result<Sample, ()> {
    Ok(decode_sample(data))
}

fn decode_sample(data: &[u8; 6]) -> Sample {
    let raw_temp = ((data[2] as i32) << 16) | ((data[1] as i32) << 8) | (data[0] as i32);
    let raw_temp = (raw_temp << 8) >> 8;
    let raw_press = ((data[5] as i32) << 16) | ((data[4] as i32) << 8) | (data[3] as i32);
    Sample {
        temp_c: raw_temp as f32 / 65536.0,
        p_pa: raw_press as f32 / 64.0,
    }
}

fn read_u8(bus: &mut I2cBus, addr: u8, reg: u8) -> Result<u8, esp_hal::i2c::master::Error> {
    let mut buf = [0u8];
    bus.write_read(addr, &[reg], &mut buf)?;
    Ok(buf[0])
}

fn write_u8(bus: &mut I2cBus, addr: u8, reg: u8, val: u8) -> Result<(), ()> {
    bus.write(addr, &[reg, val]).map_err(|_| ())
}

fn read_block(bus: &mut I2cBus, addr: u8, reg: u8, buf: &mut [u8]) -> Result<(), ()> {
    bus.write_read(addr, &[reg], buf).map_err(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn odr_table_covers_defaults() {
        assert_eq!(odr_code_for_hz(10), Some(0x17));
        assert_eq!(odr_code_for_hz(50), Some(0x0F));
    }

    #[test]
    fn decode_pt_matches_data_registers() {
        let data = [0x00, 0x00, 0x00, 0x00, 0x40, 0x00];
        let s = decode_pt_frame(&data).unwrap();
        assert!(s.p_pa > 0.0);
    }
}
