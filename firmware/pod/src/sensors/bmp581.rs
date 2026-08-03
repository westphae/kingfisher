//! Bosch BMP581 static pressure + temperature (I²C).
//!
//! NORMAL mode + on-chip FIFO (PT frames). The sensor samples at the
//! configured ODR with programmable OSR and IIR (see `crate::bmp_cfg`).

use esp_hal::delay::Delay;
use esp_println::println;
use heapless::Vec;
use pod_wire::MAX_READINGS;

use super::bus::Bus as I2cBus;
use crate::bmp_cfg;

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
const REG_DSP_CONFIG: u8 = 0x30;
const REG_DSP_IIR: u8 = 0x31;
const REG_OSR: u8 = 0x36;
const REG_ODR: u8 = 0x37;
const REG_CMD: u8 = 0x7E;

const CHIP_ID_BMP581: u8 = 0x50;
const CHIP_ID_BMP585: u8 = 0x51;
const CMD_SOFT_RESET: u8 = 0xB6;

const PWR_STANDBY: u8 = 0;
const PWR_NORMAL: u8 = 1;

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
    applied_osr_p: u8,
    applied_osr_t: u8,
    applied_iir_p: u8,
    applied_iir_t: u8,
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
                        return Some(Self {
                            addr,
                            active_hz: 0,
                            applied_osr_p: 0xff,
                            applied_osr_t: 0xff,
                            applied_iir_p: 0xff,
                            applied_iir_t: 0xff,
                        });
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

    /// Ensure NORMAL+FIFO is running at `hz` with current bmp_cfg OSR/IIR.
    pub fn ensure_odr(&mut self, bus: &mut I2cBus, hz: u16) -> Result<(), ()> {
        let hz = hz.max(1);
        let dirty = bmp_cfg::take_dirty();
        let osr_p = bmp_cfg::osr_p();
        let osr_t = bmp_cfg::osr_t();
        let iir_p = bmp_cfg::iir_p();
        let iir_t = bmp_cfg::iir_t();
        let cfg_changed = dirty
            || osr_p != self.applied_osr_p
            || osr_t != self.applied_osr_t
            || iir_p != self.applied_iir_p
            || iir_t != self.applied_iir_t;
        if self.active_hz == hz && !cfg_changed {
            return Ok(());
        }
        if hz > bmp_cfg::max_odr_hz_for_osr_p(osr_p) {
            println!(
                "pod: bmp581 ODR {hz} Hz exceeds max {} for osr_p={osr_p}",
                bmp_cfg::max_odr_hz_for_osr_p(osr_p)
            );
            return Err(());
        }
        self.configure_fifo_normal(bus, hz, osr_p, osr_t, iir_p, iir_t)?;
        self.active_hz = hz;
        self.applied_osr_p = osr_p;
        self.applied_osr_t = osr_t;
        self.applied_iir_p = iir_p;
        self.applied_iir_t = iir_t;
        println!("pod: bmp581 ODR {hz} Hz osr_p={osr_p} osr_t={osr_t} iir_p={iir_p} iir_t={iir_t}");
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

    fn configure_fifo_normal(
        &self,
        bus: &mut I2cBus,
        hz: u16,
        osr_p: u8,
        osr_t: u8,
        iir_p: u8,
        iir_t: u8,
    ) -> Result<(), ()> {
        let odr = odr_code_for_hz(hz).ok_or(())?;
        self.enter_standby(bus)?;

        write_u8(bus, self.addr, REG_INT_SOURCE, 0x01)?; // drdy_data_reg_en

        // OSR: press bits[5:3], temp bits[2:0]; bit 6 = press_en.
        let osr = ((osr_p & 0x07) << 3) | (osr_t & 0x07) | (1 << 6);
        write_u8(bus, self.addr, REG_OSR, osr)?;

        // DSP_IIR: set_iir_t bits[2:0], set_iir_p bits[5:3].
        let iir = ((iir_p & 0x07) << 3) | (iir_t & 0x07);
        write_u8(bus, self.addr, REG_DSP_IIR, iir)?;

        // Keep pressure/temp compensation (reset 0b11); route IIR into FIFO when enabled.
        let mut dsp = 0x03u8; // comp_pt_en
        if iir_p != 0 {
            dsp |= 1 << 6; // fifo_sel_iir_p
            dsp |= 1 << 5; // shdw_sel_iir_p
        }
        if iir_t != 0 {
            dsp |= 1 << 4; // fifo_sel_iir_t
            dsp |= 1 << 3; // shdw_sel_iir_t
        }
        write_u8(bus, self.addr, REG_DSP_CONFIG, dsp)?;

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
        self.applied_osr_p = 0xff;
        self.applied_osr_t = 0xff;
        self.applied_iir_p = 0xff;
        self.applied_iir_t = 0xff;
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
        assert_eq!(odr_code_for_hz(25), Some(0x14));
        assert_eq!(odr_code_for_hz(50), Some(0x0F));
    }

    #[test]
    fn decode_pt_matches_data_registers() {
        let data = [0x00, 0x00, 0x00, 0x00, 0x40, 0x00];
        let s = decode_pt_frame(&data).unwrap();
        assert!(s.p_pa > 0.0);
    }
}
