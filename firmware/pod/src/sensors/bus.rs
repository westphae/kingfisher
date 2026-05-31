//! Shared I²C bus helpers (7-bit addresses, boot-time scan).

use esp_hal::i2c::master::I2c;
use esp_println::{print, println};

pub type Bus = I2c<'static, esp_hal::Blocking>;

fn probe_reg(bus: &mut Bus, addr: u8, reg: u8, seen: &mut [bool; 128]) -> u8 {
    let mut buf = [0u8];
    match bus.write_read(addr, &[reg], &mut buf) {
        Ok(()) => {
            print!(" 0x{addr:02x}(id=0x{:02x})", buf[0]);
            seen[addr as usize] = true;
            1
        }
        Err(e) => {
            print!(" 0x{addr:02x}(err={e:?})");
            0
        }
    }
}

/// Log responding 7-bit addresses (targeted probes, then full-range write probe).
pub fn scan(bus: &mut Bus) {
    print!("pod: i2c scan:");
    let mut found = 0u8;
    let mut seen = [false; 128];
    found += probe_reg(bus, 0x47, 0x01, &mut seen);
    found += probe_reg(bus, 0x46, 0x01, &mut seen);
    found += probe_reg(bus, 0x30, 0x2f, &mut seen);
    if !seen[super::bq27441::ADDR as usize] {
        found += probe_reg(bus, super::bq27441::ADDR, 0x04, &mut seen);
    }
    if !seen[super::ms4525::ADDR as usize] {
        found += super::ms4525::Ms4525::scan_line(bus);
        seen[super::ms4525::ADDR as usize] = true;
    }
    for addr in 0x08u8..=0x77 {
        if seen[addr as usize] {
            continue;
        }
        if bus.write(addr, &[]).is_ok() {
            print!(" 0x{addr:02x}");
            found += 1;
        }
    }
    if found == 0 {
        println!(" (none)");
    } else {
        println!();
    }
}
