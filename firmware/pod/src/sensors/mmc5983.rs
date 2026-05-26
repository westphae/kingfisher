//! MEMSIC MMC5983MA magnetometer (I²C).
//!
//! Continuous measurement mode with CM_Freq tied to [`crate::rates`] SetRate.
//! Auto SET/RESET is enabled once at init; each sample is a 7-byte burst from
//! `XOUT` (no per-read control register poke). Optional INT pin support is
//! prepared via `INT_meas_done_en` when the board wires Qwiic INT.

use esp_println::println;

use super::bus::Bus as I2cBus;

pub const ADDR: u8 = 0x30;

const REG_XOUT: u8 = 0x00;
const REG_STATUS: u8 = 0x08;
const REG_IC0: u8 = 0x09;
const REG_IC1: u8 = 0x0a;
const REG_IC2: u8 = 0x0b;
const REG_PRODUCT_ID: u8 = 0x2f;

const PRODUCT_ID: u8 = 0x30;

/// Device status: magnetic measurement complete (W1C).
const STATUS_MEAS_M_DONE: u8 = 1 << 0;

/// Auto SET/RESET (IC0 bit 4).
const IC0_AUTO_SR: u8 = 0x10;
/// Pulse INT when a measurement completes (IC0 bit 3).
const IC0_INT_MEAS_DONE: u8 = 0x08;

/// Cmm_en in IC2 (bit 3).
const IC2_CMM_EN: u8 = 0x08;

/// Field vector in microtesla (wire `Reading::Mag` units).
#[derive(Debug, Clone, Copy)]
pub struct Sample {
    pub x_ut: f32,
    pub y_ut: f32,
    pub z_ut: f32,
}

pub struct Mmc5983 {
    addr: u8,
    active_hz: u16,
}

impl Mmc5983 {
    pub fn probe(bus: &mut I2cBus) -> Option<Self> {
        match read_u8(bus, ADDR, REG_PRODUCT_ID) {
            Ok(id) => {
                println!("pod: i2c 0x{ADDR:02x} product_id=0x{id:02x}");
                if id == PRODUCT_ID {
                    return Some(Self {
                        addr: ADDR,
                        active_hz: 0,
                    });
                }
            }
            Err(e) => println!("pod: i2c 0x{ADDR:02x} product_id read: {:?}", e),
        }
        None
    }

    pub fn init(&mut self, bus: &mut I2cBus) -> Result<(), ()> {
        for reg in [REG_IC0, REG_IC1, REG_IC2] {
            write_u8(bus, self.addr, reg, 0)?;
        }
        // BW[1:0]=00 (100 Hz filter), continuous mode enabled in ensure_cm_freq.
        write_u8(bus, self.addr, REG_IC0, IC0_AUTO_SR | IC0_INT_MEAS_DONE)?;
        self.active_hz = 0;
        println!("pod: mmc5983 init ok (continuous, AUTO_SR)");
        Ok(())
    }

    /// Reconfigure continuous-mode ODR to the highest CM_Freq not above `hz`.
    pub fn ensure_cm_freq(&mut self, bus: &mut I2cBus, hz: u16) -> Result<(), ()> {
        let hz = hz.max(1);
        if self.active_hz == hz {
            return Ok(());
        }
        let code = cm_freq_code_for_hz(hz).ok_or(())?;
        let ic2 = IC2_CMM_EN | code;
        write_u8(bus, self.addr, REG_IC2, ic2)?;
        self.active_hz = hz;
        println!("pod: mmc5983 CM_Freq ~{hz} Hz (IC2=0x{ic2:02x})");
        Ok(())
    }

    /// Read one magnetic sample when the data-ready status bit is set.
    pub fn read_when_ready(&self, bus: &mut I2cBus) -> Result<Option<Sample>, ()> {
        let st = read_u8(bus, self.addr, REG_STATUS)?;
        if st & STATUS_MEAS_M_DONE == 0 {
            return Ok(None);
        }
        let sample = self.read_xyz(bus)?;
        // Clear meas-done / INT (W1C).
        write_u8(bus, self.addr, REG_STATUS, STATUS_MEAS_M_DONE)?;
        Ok(Some(sample))
    }

    /// Read output registers (for poll loops that already budget a sample).
    pub fn read_sample(&self, bus: &mut I2cBus) -> Result<Sample, ()> {
        self.read_xyz(bus)
    }

    fn read_xyz(&self, bus: &mut I2cBus) -> Result<Sample, ()> {
        let mut data = [0u8; 7];
        read_block(bus, self.addr, REG_XOUT, &mut data)?;
        Ok(Sample {
            x_ut: counts_to_ut(data[0], data[1], (data[6] & 0b1100_0000) >> 6),
            y_ut: counts_to_ut(data[2], data[3], (data[6] & 0b0011_0000) >> 4),
            z_ut: counts_to_ut(data[4], data[5], (data[6] & 0b0000_1100) >> 2),
        })
    }
}

/// Map requested Hz to CM_Freq[2:0] with BW[1:0]=00 (datasheet table).
fn cm_freq_code_for_hz(hz: u16) -> Option<u8> {
    const TABLE: &[(u16, u8)] = &[
        (1, 0b001),
        (10, 0b010),
        (20, 0b011),
        (50, 0b100),
        (100, 0b101),
    ];
    let mut best: Option<(u16, u8)> = None;
    for &(h, code) in TABLE {
        if h <= hz {
            best = Some((h, code));
        }
    }
    best.map(|(_, code)| code)
}

fn counts_to_ut(msb: u8, mid: u8, lsb2: u8) -> f32 {
    let counts = ((msb as i32) << 10) | ((mid as i32) << 2) | (lsb2 as i32);
    let counts = counts - 131_072;
    counts as f32 / 163.84
}

fn read_u8(bus: &mut I2cBus, addr: u8, reg: u8) -> Result<u8, ()> {
    let mut buf = [0u8];
    bus.write_read(addr, &[reg], &mut buf).map_err(|_| ())?;
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
    use super::cm_freq_code_for_hz;

    #[test]
    fn cm_freq_codes() {
        assert_eq!(cm_freq_code_for_hz(0), None);
        assert_eq!(cm_freq_code_for_hz(1), Some(0b001));
        assert_eq!(cm_freq_code_for_hz(15), Some(0b010));
        assert_eq!(cm_freq_code_for_hz(50), Some(0b100));
        assert_eq!(cm_freq_code_for_hz(75), Some(0b101));
        assert_eq!(cm_freq_code_for_hz(200), Some(0b101));
    }
}
