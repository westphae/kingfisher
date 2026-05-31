//! Dynamic Hello caps from attached sensors.

use heapless::Vec;
use pod_wire::*;

use crate::cfg;
use crate::sensors;

pub fn build(mask: u8) -> Frame {
    let mut sensors: Vec<SensorCap, MAX_SENSORS> = Vec::new();
    if mask & sensors::BMP_BIT != 0 {
        let max = crate::rates::hello_max_hz(SensorId::Static, mask);
        let _ = sensors.push(cap(SensorId::Static, "bmp581", 1, max, 10));
    }
    if mask & sensors::MMC_BIT != 0 {
        let max = crate::rates::hello_max_hz(SensorId::Mag, mask);
        let _ = sensors.push(cap(SensorId::Mag, "mmc5983", 1, max, 10));
    }
    if mask & sensors::MS4525_BIT != 0 {
        let max = crate::rates::hello_max_hz(SensorId::Airspeed, mask);
        let _ = sensors.push(cap(SensorId::Airspeed, "ms4525", 1, max, 10));
    }
    if mask & sensors::BATTERY_BIT != 0 {
        let max = crate::rates::hello_max_hz(SensorId::Battery, mask);
        let _ = sensors.push(cap(SensorId::Battery, "bq27441", 1, max, 1));
    }
    Frame::Hello(Hello {
        fw_version: cfg::FW_VERSION,
        proto_version: PROTO_VERSION,
        caps: Capabilities { sensors },
    })
}

fn cap(id: SensorId, name: &str, min_hz: u16, max_hz: u16, default_hz: u16) -> SensorCap {
    SensorCap {
        id,
        min_hz,
        max_hz,
        default_hz,
        device_name: SensorDeviceName::new(name),
    }
}
