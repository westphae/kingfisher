//! Bosch BMP581 static pressure + temperature (I²C).
//!
//! Register map and bring-up follow ESPHome `bmp581_base` (BSD-3-Clause), which
//! tracks BST-BMP581-DS004.

use esp_hal::delay::Delay;
use esp_println::println;

use super::bus::Bus as I2cBus;

pub const ADDR_PRIMARY: u8 = 0x46;
pub const ADDR_SECONDARY: u8 = 0x47;

const REG_CHIP_ID: u8 = 0x01;
const REG_INT_SOURCE: u8 = 0x15;
const REG_MEASUREMENT: u8 = 0x1D;
const REG_INT_STATUS: u8 = 0x27;
const REG_STATUS: u8 = 0x28;
const REG_OSR: u8 = 0x36;
const REG_ODR: u8 = 0x37;
const REG_CMD: u8 = 0x7E;

const CHIP_ID_BMP581: u8 = 0x50;
const CHIP_ID_BMP585: u8 = 0x51;
const CMD_SOFT_RESET: u8 = 0xB6;

const PWR_STANDBY: u8 = 0;
const PWR_FORCED: u8 = 2;

const OSR_TEMP_NONE: u8 = 0;
const OSR_PRESS_NONE: u8 = 0;

/// Pressure [Pa] and temperature [°C] from the latest forced conversion.
#[derive(Debug, Clone, Copy)]
pub struct Sample {
    pub p_pa: f32,
    pub temp_c: f32,
}

pub struct Bmp581 {
    addr: u8,
}

impl Bmp581 {
    pub fn addr(&self) -> u8 {
        self.addr
    }

    /// SDO high → 0x47; SDO low → 0x46. Probe the strap you use first.
    pub fn probe(bus: &mut I2cBus) -> Option<Self> {
        for addr in [ADDR_SECONDARY, ADDR_PRIMARY] {
            match read_u8(bus, addr, REG_CHIP_ID) {
                Ok(id) => {
                    println!("pod: i2c 0x{addr:02x} chip_id=0x{id:02x}");
                    if id == CHIP_ID_BMP581 || id == CHIP_ID_BMP585 {
                        return Some(Self { addr });
                    }
                }
                Err(e) => println!("pod: i2c 0x{addr:02x} chip_id read: {:?}", e),
            }
        }
        None
    }

    pub fn init(&self, bus: &mut I2cBus) -> Result<(), ()> {
        // ESPHome bring-up: soft reset → chip id → NVM status → enable DRDY.
        self.soft_reset(bus)?;
        let id = read_u8(bus, self.addr, REG_CHIP_ID).map_err(|_| ())?;
        if id != CHIP_ID_BMP581 && id != CHIP_ID_BMP585 {
            println!("pod: bmp581 bad chip_id 0x{id:02x}");
            return Err(());
        }
        self.wait_nvm_ready(bus)?;
        write_u8(bus, self.addr, REG_INT_SOURCE, 0x01)?; // drdy_data_reg_en
        // Temp + pressure, no oversampling (fastest forced reads).
        let osr = (OSR_PRESS_NONE << 3) | (OSR_TEMP_NONE << 0) | (1 << 6);
        write_u8(bus, self.addr, REG_OSR, osr)?;
        write_u8(bus, self.addr, REG_ODR, PWR_STANDBY)?;
        println!("pod: bmp581 init ok at 0x{:02x}", self.addr);
        Ok(())
    }

    pub fn read(&self, bus: &mut I2cBus) -> Result<Sample, ()> {
        write_u8(bus, self.addr, REG_ODR, PWR_FORCED)?;
        // No OSR: ~3 ms conversion; use margin for bus + scheduling.
        Delay::new().delay_micros(25_000);
        if !self.data_ready(bus)? {
            return Err(());
        }
        let mut data = [0u8; 6];
        read_block(bus, self.addr, REG_MEASUREMENT, &mut data)?;
        Ok(decode_sample(&data))
    }

    fn soft_reset(&self, bus: &mut I2cBus) -> Result<(), ()> {
        write_u8(bus, self.addr, REG_CMD, CMD_SOFT_RESET)?;
        Delay::new().delay_micros(3_000);
        let st = read_u8(bus, self.addr, REG_INT_STATUS).map_err(|_| ())?;
        if st & 0x10 == 0 {
            println!("pod: bmp581 reset: POR not set (int_status=0x{st:02x})");
            return Err(());
        }
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

    fn data_ready(&self, bus: &mut I2cBus) -> Result<bool, ()> {
        let st = read_u8(bus, self.addr, REG_INT_STATUS).map_err(|_| ())?;
        Ok(st & 0x01 != 0)
    }
}

fn decode_sample(data: &[u8; 6]) -> Sample {
    let raw_temp =
        ((data[2] as i32) << 16) | ((data[1] as i32) << 8) | (data[0] as i32);
    let raw_temp = (raw_temp << 8) >> 8;
    let raw_press =
        ((data[5] as i32) << 16) | ((data[4] as i32) << 8) | (data[3] as i32);
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

fn read_block(
    bus: &mut I2cBus,
    addr: u8,
    reg: u8,
    buf: &mut [u8],
) -> Result<(), ()> {
    bus.write_read(addr, &[reg], buf).map_err(|_| ())
}
