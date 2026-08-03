//! Inbound `Cmd` handling and `Ack` replies.

use core::sync::atomic::{AtomicU32, Ordering};

use pod_wire::{Ack, AttrKey, Cmd, CmdEnvelope, Frame, SensorId};

use crate::battery_cfg;
use crate::bmp_cfg;
use crate::link;
use crate::rates;
use crate::sensors;

static RX_SEQ_LAST: AtomicU32 = AtomicU32::new(0);

pub fn last_rx_cmd_seq() -> u32 {
    RX_SEQ_LAST.load(Ordering::Relaxed)
}

/// Sensor rate limits advertised in Hello (must match `hello::cap` defaults).
fn rate_ok(sensor: SensorId, hz: u16) -> bool {
    let (min, max) = match sensor {
        SensorId::Static | SensorId::Airspeed => (1, 50),
        SensorId::Mag => (1, 100),
        SensorId::Battery => (1, 2),
    };
    hz >= min && hz <= max
}

/// Apply one command; returns the Ack to send (seq from envelope).
pub fn handle(envelope: CmdEnvelope) -> Ack {
    let seq = envelope.seq;
    RX_SEQ_LAST.store(seq, Ordering::Relaxed);
    let ok = match envelope.cmd {
        Cmd::SetRate { sensor, hz } => {
            if !rate_ok(sensor, hz) || !sensor_attached(sensor) {
                false
            } else if sensor == SensorId::Static && hz > bmp_cfg::max_odr_hz_for_osr_p(bmp_cfg::osr_p())
            {
                false
            } else {
                rates::try_set(sensor, hz)
            }
        }
        Cmd::SetAttr { sensor, key, value } => match (sensor, key) {
            (SensorId::Battery, AttrKey::DesignCapacity) => {
                if !sensor_attached(SensorId::Battery) {
                    false
                } else {
                    battery_cfg::request_design_mah(value as u16)
                }
            }
            (SensorId::Static, AttrKey::BmpOsrPress) => {
                sensor_attached(SensorId::Static) && bmp_cfg::request_osr_p_mult(value)
            }
            (SensorId::Static, AttrKey::BmpOsrTemp) => {
                sensor_attached(SensorId::Static) && bmp_cfg::request_osr_t_mult(value)
            }
            (SensorId::Static, AttrKey::BmpIirPress) => {
                sensor_attached(SensorId::Static) && bmp_cfg::request_iir_p_coeff(value)
            }
            (SensorId::Static, AttrKey::BmpIirTemp) => {
                sensor_attached(SensorId::Static) && bmp_cfg::request_iir_t_coeff(value)
            }
            _ => false,
        },
    };
    Ack { for_seq: seq, ok }
}

fn sensor_attached(sensor: SensorId) -> bool {
    let mask = sensors::attached_mask();
    match sensor {
        SensorId::Static => mask & sensors::BMP_BIT != 0,
        SensorId::Mag => mask & sensors::MMC_BIT != 0,
        SensorId::Airspeed => mask & sensors::MS4525_BIT != 0,
        SensorId::Battery => mask & sensors::BATTERY_BIT != 0,
    }
}

/// Decode and handle one inbound datagram; returns frames to transmit (Pong, Ack).
pub fn handle_datagram(bytes: &[u8], now_us: u64, uptime_us: u64) -> heapless::Vec<Frame, 4> {
    use heapless::Vec;

    let mut out: Vec<Frame, 4> = Vec::new();
    let frame = match pod_wire::decode_from_slice(bytes) {
        Ok(f) => f,
        Err(_) => return out,
    };

    match frame {
        Frame::Ping(p) => {
            // Pings are the Pi's 5 s keepalive: they must refresh last_inbound
            // so the 30 s Hello rediscovery stays suppressed (touch_inbound
            // SUPPRESSES Hellos — a pod seeing only pings would otherwise
            // re-Hello forever, and each Hello re-triggers SetRate churn).
            link::touch_inbound(now_us);
            let _ = out.push(Frame::Pong(pod_wire::Pong {
                seq: p.seq,
                sender_uptime_us: uptime_us,
                echo_uptime_us: p.sender_uptime_us,
            }));
        }
        Frame::Cmd(env) => {
            link::touch_inbound(now_us);
            let ack = handle(env);
            let _ = out.push(Frame::Ack(ack));
        }
        _ => {}
    }
    out
}
