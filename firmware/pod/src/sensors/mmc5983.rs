//! MEMSIC MMC5983MA magnetometer (I²C).
//!
//! Register map and bring-up follow ESPHome `mmc5983` (Apache-2.0), which
//! tracks the SparkFun Arduino library and MMC5983MA datasheet.

use esp_println::println;

use super::bus::Bus as I2cBus;

pub const ADDR: u8 = 0x30;

const REG_XOUT: u8 = 0x00;
const REG_IC0: u8 = 0x09;
const REG_IC1: u8 = 0x0a;
const REG_IC2: u8 = 0x0b;
const REG_IC3: u8 = 0x0c;
const REG_PRODUCT_ID: u8 = 0x2f;

const PRODUCT_ID: u8 = 0x30;

/// Auto SET/RESET before read (IC0 bit 4).
const IC0_AUTO_SR: u8 = 0x10;
/// Continuous mode @ 100 Hz (IC2: Cmm_en + Cm_freq=101).
const IC2_CONTINUOUS_100HZ: u8 = 0x0d;

/// Field vector in microtesla (wire `Reading::Mag` units).
#[derive(Debug, Clone, Copy)]
pub struct Sample {
    pub x_ut: f32,
    pub y_ut: f32,
    pub z_ut: f32,
}

pub struct Mmc5983 {
    addr: u8,
}

impl Mmc5983 {
    pub fn probe(bus: &mut I2cBus) -> Option<Self> {
        match read_u8(bus, ADDR, REG_PRODUCT_ID) {
            Ok(id) => {
                println!("pod: i2c 0x{ADDR:02x} product_id=0x{id:02x}");
                if id == PRODUCT_ID {
                    return Some(Self { addr: ADDR });
                }
            }
            Err(e) => println!("pod: i2c 0x{ADDR:02x} product_id read: {:?}", e),
        }
        None
    }

    pub fn init(&self, bus: &mut I2cBus) -> Result<(), ()> {
        for reg in [REG_IC0, REG_IC1, REG_IC2, REG_IC3] {
            write_u8(bus, self.addr, reg, 0)?;
        }
        write_u8(bus, self.addr, REG_IC2, IC2_CONTINUOUS_100HZ)?;
        println!("pod: mmc5983 init ok");
        Ok(())
    }

    pub fn read(&self, bus: &mut I2cBus) -> Result<Sample, ()> {
        write_u8(bus, self.addr, REG_IC0, IC0_AUTO_SR)?;
        let mut data = [0u8; 7];
        read_block(bus, self.addr, REG_XOUT, &mut data)?;
        Ok(Sample {
            x_ut: counts_to_ut(data[0], data[1], (data[6] & 0b1100_0000) >> 6),
            y_ut: counts_to_ut(data[2], data[3], (data[6] & 0b0011_0000) >> 4),
            z_ut: counts_to_ut(data[4], data[5], (data[6] & 0b0000_1100) >> 2),
        })
    }
}

fn counts_to_ut(msb: u8, mid: u8, lsb2: u8) -> f32 {
    let counts = ((msb as i32) << 10) | ((mid as i32) << 2) | (lsb2 as i32);
    let counts = counts - 131_072; // null field output
                                   // 16384 counts/Gauss, 1 G = 100 µT
    counts as f32 / 163.84
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
