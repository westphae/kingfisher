//! SparkFun Battery Babysitter / TI BQ27441-G1A fuel gauge (I²C).
//!
//! Standard command reads only; design capacity is written once when the
//! ITPOR flag indicates the gauge lost power.

use embassy_time::{block_for, Duration, Instant};
use esp_println::println;

use super::bus::Bus as I2cBus;

pub const ADDR: u8 = 0x55;

const CMD_CONTROL: u8 = 0x00;
const CMD_VOLTAGE: u8 = 0x04;
const CMD_FLAGS: u8 = 0x06;
const CMD_REM_CAPACITY: u8 = 0x0c;
const CMD_FULL_CAPACITY: u8 = 0x0e;
const CMD_DESIGN_CAPACITY: u8 = 0x3c;
const CMD_AVG_CURRENT: u8 = 0x10;
const CMD_AVG_POWER: u8 = 0x18;
const CMD_SOC: u8 = 0x1c;

const CTRL_DEVICE_TYPE: u16 = 0x0001;
const CTRL_UNSEAL_KEY: u16 = 0x8000;
const CTRL_SET_CFGUPDATE: u16 = 0x0013;
const CTRL_SOFT_RESET: u16 = 0x0042;

const EXT_CONTROL: u8 = 0x61;
const EXT_DATACLASS: u8 = 0x3e;
const EXT_DATABLOCK: u8 = 0x3f;
const EXT_BLOCKDATA: u8 = 0x40;
const EXT_CHECKSUM: u8 = 0x60;

const ID_STATE: u8 = 82;
const DESIGN_CAPACITY_OFFSET: u8 = 10;

/// State subclass stores 16-bit fields MSB-first (TI TRM / SparkFun library).
fn u16_be_bytes(v: u16) -> [u8; 2] {
    [(v >> 8) as u8, (v & 0xff) as u8]
}

/// Design energy (mWh) from mAh at 3.7 V nominal LiPo.
fn design_energy_mwh(mah: u16) -> u16 {
    ((mah as u32) * 37 / 10) as u16
}

const DEVICE_ID: u16 = 0x0421;
const FLAG_ITPOR: u16 = 1 << 5;
const FLAG_CFGUPMODE: u16 = 1 << 4;

const I2C_GAP_US: u64 = 70;
const MIN_READ_INTERVAL_US: u64 = 500_000;

/// One fuel-gauge sample mapped to wire [`Reading::Battery`] units.
#[derive(Debug, Clone, Copy)]
pub struct Sample {
    pub voltage_v: f32,
    pub current_a: f32,
    pub power_w: f32,
    pub capacity_remain_mah: f32,
    pub capacity_full_mah: f32,
    pub soc_pct: f32,
    pub time_remain_s: f32,
    pub design_capacity_mah: u16,
}

pub struct Bq27441 {
    addr: u8,
    last_read_at: Option<Instant>,
}

impl Bq27441 {
    pub fn probe(bus: &mut I2cBus) -> Option<Self> {
        match read_control_word(bus, ADDR, CTRL_DEVICE_TYPE) {
            Ok(id) => {
                println!("pod: i2c 0x{ADDR:02x} device_type=0x{id:04x}");
                if id == DEVICE_ID {
                    return Some(Self {
                        addr: ADDR,
                        last_read_at: None,
                    });
                }
            }
            Err(e) => println!("pod: i2c 0x{ADDR:02x} device_type read: {:?}", e),
        }
        None
    }

    pub fn init(&mut self, bus: &mut I2cBus) -> Result<(), ()> {
        let flags = read_word(bus, self.addr, CMD_FLAGS)?;
        let fcc = read_word(bus, self.addr, CMD_FULL_CAPACITY)?;
        let design_reg = read_design_capacity(bus, self.addr)?;
        let design = crate::battery_cfg::design_mah();
        let itpor = flags & FLAG_ITPOR != 0;
        if itpor {
            println!(
                "pod: bq27441 configuring design capacity {} mAh (ITPOR design_reg={design_reg} fcc={fcc})",
                design
            );
            if set_design_capacity(bus, self.addr, design).is_ok() {
                crate::battery_cfg::note_program_ok();
            } else {
                println!("pod: bq27441 ITPOR design capacity program failed (continuing)");
                let _ = crate::battery_cfg::request_design_mah(design);
            }
        } else {
            crate::battery_cfg::queue_design_if_mismatch(design_reg);
            if design_reg == 0 {
                println!(
                    "pod: bq27441 design_reg=0; queued design {} mAh for poll thread",
                    design
                );
            } else if design_reg != design {
                println!(
                    "pod: bq27441 design_reg={design_reg} mAh != target {design} mAh; queued reprogram"
                );
            }
        }
        self.last_read_at = None;
        println!("pod: bq27441 init ok");
        Ok(())
    }

    /// Reprogram design capacity (mAh) while running; called from the poll loop.
    pub fn program_design_capacity(&self, bus: &mut I2cBus, mah: u16) -> Result<(), ()> {
        set_design_capacity(bus, self.addr, mah)
    }

    /// Read all headline metrics when the BQ27441 rate limit allows.
    pub fn read_when_due(&mut self, bus: &mut I2cBus) -> Result<Option<Sample>, ()> {
        let now = Instant::now();
        if let Some(prev) = self.last_read_at {
            if now.duration_since(prev).as_micros() < MIN_READ_INTERVAL_US {
                return Ok(None);
            }
        }
        let sample = self.read_sample(bus)?;
        self.last_read_at = Some(now);
        Ok(Some(sample))
    }

    fn read_sample(&self, bus: &mut I2cBus) -> Result<Sample, ()> {
        let voltage_mv = read_word(bus, self.addr, CMD_VOLTAGE)?;
        let capacity_remain = read_word(bus, self.addr, CMD_REM_CAPACITY)?;
        let capacity_full = read_word(bus, self.addr, CMD_FULL_CAPACITY)?;
        let current_ma = read_word_signed(bus, self.addr, CMD_AVG_CURRENT)?;
        let power_mw = read_word_signed(bus, self.addr, CMD_AVG_POWER)?;
        let soc = read_word(bus, self.addr, CMD_SOC)?;
        let design_capacity_mah = read_design_capacity(bus, self.addr)?;

        let voltage_v = voltage_mv as f32 / 1000.0;
        let current_a = current_ma as f32 / 1000.0;
        let power_w = power_mw as f32 / 1000.0;
        let capacity_remain_mah = capacity_remain as f32;
        let capacity_full_mah = capacity_full as f32;
        let soc_pct = soc as f32;

        // mAh / mA -> hours; ×3600 -> seconds (not mAh/A, which is 1000× too large).
        let time_remain_s = if current_ma < -1 {
            (capacity_remain_mah / (current_ma as f32).abs()) * 3600.0
        } else {
            -1.0
        };

        Ok(Sample {
            voltage_v,
            current_a,
            power_w,
            capacity_remain_mah,
            capacity_full_mah,
            soc_pct,
            time_remain_s,
            design_capacity_mah,
        })
    }
}

fn i2c_gap() {
    block_for(Duration::from_micros(I2C_GAP_US));
}

fn read_word(bus: &mut I2cBus, addr: u8, reg: u8) -> Result<u16, ()> {
    let mut buf = [0u8; 2];
    bus.write_read(addr, &[reg], &mut buf).map_err(|_| ())?;
    i2c_gap();
    Ok(u16::from_le_bytes(buf))
}

fn read_word_signed(bus: &mut I2cBus, addr: u8, reg: u8) -> Result<i16, ()> {
    Ok(read_word(bus, addr, reg)? as i16)
}

fn write_byte(bus: &mut I2cBus, addr: u8, reg: u8, val: u8) -> Result<(), ()> {
    bus.write(addr, &[reg, val]).map_err(|_| ())?;
    i2c_gap();
    Ok(())
}

fn read_byte(bus: &mut I2cBus, addr: u8, reg: u8) -> Result<u8, ()> {
    let mut buf = [0u8];
    bus.write_read(addr, &[reg], &mut buf).map_err(|_| ())?;
    i2c_gap();
    Ok(buf[0])
}

/// Data-memory Design Capacity (mAh), not learned FullChargeCapacity (0x0E).
fn read_design_capacity(bus: &mut I2cBus, addr: u8) -> Result<u16, ()> {
    read_word(bus, addr, CMD_DESIGN_CAPACITY)
}

fn read_control_word(bus: &mut I2cBus, addr: u8, function: u16) -> Result<u16, ()> {
    let cmd = function.to_le_bytes();
    bus.write(addr, &[CMD_CONTROL, cmd[0], cmd[1]]).map_err(|_| ())?;
    i2c_gap();
    read_word(bus, addr, CMD_CONTROL)
}

fn execute_control(bus: &mut I2cBus, addr: u8, function: u16) -> Result<(), ()> {
    let cmd = function.to_le_bytes();
    bus.write(addr, &[CMD_CONTROL, cmd[0], cmd[1]]).map_err(|_| ())?;
    i2c_gap();
    Ok(())
}

fn enter_config(bus: &mut I2cBus, addr: u8) -> Result<(), ()> {
    // Unseal twice (SparkFun/TI); required when the gauge is in sealed mode.
    execute_control(bus, addr, CTRL_UNSEAL_KEY)?;
    execute_control(bus, addr, CTRL_UNSEAL_KEY)?;
    execute_control(bus, addr, CTRL_SET_CFGUPDATE)?;
    for _ in 0..2000 {
        let flags = read_word(bus, addr, CMD_FLAGS)?;
        if flags & FLAG_CFGUPMODE != 0 {
            return Ok(());
        }
        block_for(Duration::from_millis(1));
    }
    Err(())
}

fn exit_config_resim(bus: &mut I2cBus, addr: u8) -> Result<(), ()> {
    execute_control(bus, addr, CTRL_SOFT_RESET)?;
    for _ in 0..2000 {
        let flags = read_word(bus, addr, CMD_FLAGS)?;
        if flags & FLAG_CFGUPMODE == 0 {
            return Ok(());
        }
        block_for(Duration::from_millis(1));
    }
    Err(())
}

fn read_block_window(bus: &mut I2cBus, addr: u8) -> Result<[u8; 32], ()> {
    let mut block = [0u8; 32];
    for (i, b) in block.iter_mut().enumerate() {
        *b = read_byte(bus, addr, EXT_BLOCKDATA + i as u8)?;
    }
    Ok(block)
}

fn select_data_block(bus: &mut I2cBus, addr: u8, class_id: u8, offset: u8) -> Result<(), ()> {
    write_byte(bus, addr, EXT_CONTROL, 0x00)?;
    write_byte(bus, addr, EXT_DATACLASS, class_id)?;
    write_byte(bus, addr, EXT_DATABLOCK, offset / 32)?;
    Ok(())
}

fn write_extended_bytes(
    bus: &mut I2cBus,
    addr: u8,
    class_id: u8,
    offset: u8,
    data: &[u8],
) -> Result<(), ()> {
    if data.is_empty() || data.len() > 32 {
        return Err(());
    }
    let base = (offset % 32) as usize;
    if base + data.len() > 32 {
        return Err(());
    }

    select_data_block(bus, addr, class_id, offset)?;
    let _ = read_block_window(bus, addr)?;

    for (i, &b) in data.iter().enumerate() {
        write_byte(bus, addr, EXT_BLOCKDATA + base as u8 + i as u8, b)?;
    }

    let block = read_block_window(bus, addr)?;
    let mut sum: u8 = 0;
    for b in block {
        sum = sum.wrapping_add(b);
    }
    let csum = 255_u8.wrapping_sub(sum);
    write_byte(bus, addr, EXT_CHECKSUM, csum)?;
    Ok(())
}

#[derive(Copy, Clone)]
enum ProgramFail {
    EnterConfig,
    WriteBlock,
    ExitConfig,
    Verify,
}

fn set_design_capacity(bus: &mut I2cBus, addr: u8, mah: u16) -> Result<(), ()> {
    let energy = design_energy_mwh(mah);
    let mut payload = [0u8; 4];
    payload[0..2].copy_from_slice(&u16_be_bytes(mah));
    payload[2..4].copy_from_slice(&u16_be_bytes(energy));

    if enter_config(bus, addr).is_err() {
        log_program_fail(addr, mah, ProgramFail::EnterConfig, 0);
        return Err(());
    }
    if write_extended_bytes(
        bus,
        addr,
        ID_STATE,
        DESIGN_CAPACITY_OFFSET,
        &payload,
    )
    .is_err()
    {
        log_program_fail(addr, mah, ProgramFail::WriteBlock, 0);
        let _ = exit_config_resim(bus, addr);
        return Err(());
    }
    if exit_config_resim(bus, addr).is_err() {
        log_program_fail(addr, mah, ProgramFail::ExitConfig, 0);
        return Err(());
    }
    block_for(Duration::from_millis(500));
    for attempt in 0..40 {
        match read_design_capacity(bus, addr) {
            Ok(reg) if reg == mah => return Ok(()),
            Ok(reg) => {
                if attempt == 39 {
                    log_program_fail(addr, mah, ProgramFail::Verify, reg);
                }
            }
            Err(()) => {
                if attempt == 39 {
                    log_program_fail(addr, mah, ProgramFail::Verify, 0);
                }
            }
        }
        block_for(Duration::from_millis(50));
    }
    Err(())
}

fn log_program_fail(_addr: u8, target_mah: u16, stage: ProgramFail, design_reg: u16) {
    let stage_s = match stage {
        ProgramFail::EnterConfig => "enter_config",
        ProgramFail::WriteBlock => "write_block",
        ProgramFail::ExitConfig => "exit_config",
        ProgramFail::Verify => "verify_design_reg",
    };
    println!(
        "pod: bq27441 design program fail stage={stage_s} target={target_mah} design_reg=0x{design_reg:04x} ({design_reg})"
    );
}
