//! Shared I²C bus helpers (7-bit addresses, boot-time scan).

use esp_hal::i2c::master::I2c;
use esp_println::{print, println};

pub type Bus = I2c<'static, esp_hal::Blocking>;

/// Log responding 7-bit addresses (register read at BMP581 addresses, then
/// full-range write probe for anything else).
pub fn scan(bus: &mut Bus) {
    print!("pod: i2c scan:");
    let mut found = 0u8;
    let mut seen = [false; 128];
    for addr in [0x47u8, 0x46] {
        let mut buf = [0u8];
        match bus.write_read(addr, &[0x01], &mut buf) {
            Ok(()) => {
                print!(" 0x{addr:02x}(id=0x{:02x})", buf[0]);
                seen[addr as usize] = true;
                found += 1;
            }
            Err(e) => print!(" 0x{addr:02x}(err={e:?})"),
        }
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
