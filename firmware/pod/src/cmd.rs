//! Inbound `Cmd` handling and `Ack` replies.

use core::sync::atomic::{AtomicU32, Ordering};

use pod_wire::{Ack, Cmd, CmdEnvelope, Frame, SensorId};

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
        SensorId::Static | SensorId::Mag | SensorId::Airspeed => (1, 50),
    };
    hz >= min && hz <= max
}

/// Apply one command; returns the Ack to send (seq from envelope).
pub fn handle(envelope: CmdEnvelope) -> Ack {
    let seq = envelope.seq;
    RX_SEQ_LAST.store(seq, Ordering::Relaxed);
    let ok = match envelope.cmd {
        Cmd::SetRate { sensor, hz } => {
            if !rate_ok(sensor, hz) {
                false
            } else if !sensor_attached(sensor) {
                false
            } else {
                rates::try_set(sensor, hz)
            }
        }
        Cmd::SetAttr { .. } => false,
        Cmd::Ping => true,
        Cmd::Reboot => false,
    };
    Ack { for_seq: seq, ok }
}

fn sensor_attached(sensor: SensorId) -> bool {
    let mask = sensors::attached_mask();
    match sensor {
        SensorId::Static => mask & sensors::BMP_BIT != 0,
        SensorId::Mag => mask & sensors::MMC_BIT != 0,
        SensorId::Airspeed => mask & sensors::MS4525_BIT != 0,
    }
}

/// Decode and handle one inbound datagram; returns frames to transmit (Pong, Ack).
pub fn handle_datagram(
    bytes: &[u8],
    now_us: u64,
    uptime_us: u64,
) -> heapless::Vec<Frame, 4> {
    use heapless::Vec;

    let mut out: Vec<Frame, 4> = Vec::new();
    let frame = match pod_wire::decode_from_slice(bytes) {
        Ok(f) => f,
        Err(_) => return out,
    };

    link::touch_inbound(now_us);

    match frame {
        Frame::Ping(p) => {
            // Pi may start after our boot Hello; re-advertise caps on link-up.
            link::request_hello();
            let _ = out.push(Frame::Pong(pod_wire::Pong {
                seq: p.seq,
                sender_uptime_us: uptime_us,
                echo_uptime_us: p.sender_uptime_us,
            }));
        }
        Frame::Cmd(env) => {
            let ack = handle(env);
            let _ = out.push(Frame::Ack(ack));
        }
        _ => {}
    }
    out
}
