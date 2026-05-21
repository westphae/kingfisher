//! Shared wire format between the wing pod (ESP32-C3) and kingfisher (Pi).
//!
//! Every datagram on the wire is:
//!
//! ```text
//! [u16 LE: body length] [postcard-encoded Frame: body] [u32 LE: crc32(body)]
//! ```
//!
//! The Go decoder in `internal/pod/wire/` mirrors this layout exactly.

#![no_std]

use serde::{Deserialize, Serialize};

pub const PROTO_VERSION: u8 = 1;
pub const MAX_READINGS: usize = 8;
pub const MAX_SENSORS: usize = 4;

pub const HEADER_LEN: usize = 2;
pub const CRC_LEN: usize = 4;
pub const FRAMING_OVERHEAD: usize = HEADER_LEN + CRC_LEN;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum Frame {
    Hello(Hello),
    Status(Status),
    Sample(SampleBatch),
    Cmd(CmdEnvelope),
    Ack(Ack),
    Ping(Ping),
    Pong(Pong),
}

/// Pi-assigned sequence number for a [`Cmd`]; echoed in [`Ack::for_seq`].
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CmdEnvelope {
    pub seq: u32,
    pub cmd: Cmd,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Hello {
    pub fw_version: u32,
    pub proto_version: u8,
    pub caps: Capabilities,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Capabilities {
    pub sensors: heapless::Vec<SensorCap, MAX_SENSORS>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SensorCap {
    pub id: SensorId,
    pub min_hz: u16,
    pub max_hz: u16,
    pub default_hz: u16,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum SensorId {
    Airspeed,
    Static,
    Mag,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Status {
    pub pod_uptime_us: u64,
    pub battery_v: f32,
    pub rssi_dbm: i8,
    pub tx_seq: u32,
    pub rx_seq_last: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SampleBatch {
    pub pod_uptime_us: u64,
    pub seq: u32,
    pub samples: heapless::Vec<Reading, MAX_READINGS>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum Reading {
    Airspeed {
        dp_pa: f32,
        temp_c: f32,
        age_us: u32,
    },
    Static {
        p_pa: f32,
        temp_c: f32,
        age_us: u32,
    },
    Mag {
        x_ut: f32,
        y_ut: f32,
        z_ut: f32,
        age_us: u32,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum Cmd {
    SetRate { sensor: SensorId, hz: u16 },
    SetAttr { sensor: SensorId, key: AttrKey, value: f32 },
    Ping,
    Reboot,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum AttrKey {
    Oversampling,
    IirFilter,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Ack {
    pub for_seq: u32,
    pub ok: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Ping {
    pub seq: u32,
    pub sender_uptime_us: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Pong {
    pub seq: u32,
    pub sender_uptime_us: u64,
    pub echo_uptime_us: u64,
}

#[derive(Debug)]
pub enum Error {
    /// Output buffer too small or input shorter than framing overhead / declared length.
    Buffer,
    /// CRC32 mismatch.
    Crc,
    /// Postcard codec rejected the body.
    Postcard(postcard::Error),
}

impl From<postcard::Error> for Error {
    fn from(e: postcard::Error) -> Self {
        Error::Postcard(e)
    }
}

/// Encode `frame` into `out` with framing wrapper.
/// Returns the subslice containing the full datagram.
pub fn encode_to_slice<'a>(frame: &Frame, out: &'a mut [u8]) -> Result<&'a [u8], Error> {
    if out.len() < FRAMING_OVERHEAD {
        return Err(Error::Buffer);
    }
    let buf_len = out.len();
    let body_len = {
        let body_buf = &mut out[HEADER_LEN..buf_len - CRC_LEN];
        postcard::to_slice(frame, body_buf)?.len()
    };
    let crc = crc32fast::hash(&out[HEADER_LEN..HEADER_LEN + body_len]);
    out[0..HEADER_LEN].copy_from_slice(&(body_len as u16).to_le_bytes());
    out[HEADER_LEN + body_len..HEADER_LEN + body_len + CRC_LEN]
        .copy_from_slice(&crc.to_le_bytes());
    Ok(&out[..HEADER_LEN + body_len + CRC_LEN])
}

/// Decode a complete framed datagram. Rejects short inputs and CRC mismatches.
pub fn decode_from_slice(input: &[u8]) -> Result<Frame, Error> {
    if input.len() < FRAMING_OVERHEAD {
        return Err(Error::Buffer);
    }
    let body_len = u16::from_le_bytes([input[0], input[1]]) as usize;
    if input.len() < HEADER_LEN + body_len + CRC_LEN {
        return Err(Error::Buffer);
    }
    let body = &input[HEADER_LEN..HEADER_LEN + body_len];
    let crc_bytes = &input[HEADER_LEN + body_len..HEADER_LEN + body_len + CRC_LEN];
    let crc_have = u32::from_le_bytes([crc_bytes[0], crc_bytes[1], crc_bytes[2], crc_bytes[3]]);
    let crc_want = crc32fast::hash(body);
    if crc_have != crc_want {
        return Err(Error::Crc);
    }
    Ok(postcard::from_bytes(body)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rt(frame: &Frame) {
        let mut buf = [0u8; 512];
        let n = encode_to_slice(frame, &mut buf).unwrap().len();
        let decoded = decode_from_slice(&buf[..n]).unwrap();
        assert_eq!(*frame, decoded);
    }

    #[test]
    fn roundtrip_hello() {
        let mut sensors = heapless::Vec::new();
        sensors
            .push(SensorCap {
                id: SensorId::Airspeed,
                min_hz: 1,
                max_hz: 50,
                default_hz: 10,
            })
            .unwrap();
        sensors
            .push(SensorCap {
                id: SensorId::Mag,
                min_hz: 1,
                max_hz: 200,
                default_hz: 50,
            })
            .unwrap();
        rt(&Frame::Hello(Hello {
            fw_version: 0x0001_0203,
            proto_version: PROTO_VERSION,
            caps: Capabilities { sensors },
        }));
    }

    #[test]
    fn roundtrip_sample_batch() {
        let mut samples = heapless::Vec::new();
        samples
            .push(Reading::Airspeed {
                dp_pa: 102.5,
                temp_c: 18.3,
                age_us: 250,
            })
            .unwrap();
        samples
            .push(Reading::Static {
                p_pa: 98_765.0,
                temp_c: 18.4,
                age_us: 100,
            })
            .unwrap();
        samples
            .push(Reading::Mag {
                x_ut: 21.3,
                y_ut: -4.1,
                z_ut: 42.8,
                age_us: 0,
            })
            .unwrap();
        rt(&Frame::Sample(SampleBatch {
            pod_uptime_us: 1_234_567_890,
            seq: 42,
            samples,
        }));
    }

    #[test]
    fn roundtrip_cmd() {
        rt(&Frame::Cmd(CmdEnvelope {
            seq: 1,
            cmd: Cmd::SetRate {
                sensor: SensorId::Mag,
                hz: 50,
            },
        }));
        rt(&Frame::Cmd(CmdEnvelope {
            seq: 2,
            cmd: Cmd::SetAttr {
                sensor: SensorId::Static,
                key: AttrKey::Oversampling,
                value: 16.0,
            },
        }));
        rt(&Frame::Cmd(CmdEnvelope {
            seq: 3,
            cmd: Cmd::Reboot,
        }));
    }

    #[test]
    fn roundtrip_ping_pong_status_ack() {
        rt(&Frame::Ping(Ping {
            seq: 7,
            sender_uptime_us: 999,
        }));
        rt(&Frame::Pong(Pong {
            seq: 7,
            sender_uptime_us: 1100,
            echo_uptime_us: 999,
        }));
        rt(&Frame::Status(Status {
            pod_uptime_us: 5_000_000,
            battery_v: 3.78,
            rssi_dbm: -64,
            tx_seq: 100,
            rx_seq_last: 12,
        }));
        rt(&Frame::Ack(Ack {
            for_seq: 99,
            ok: true,
        }));
    }

    #[test]
    fn crc_mismatch_rejected() {
        let mut buf = [0u8; 64];
        let n = encode_to_slice(
            &Frame::Cmd(CmdEnvelope {
                seq: 4,
                cmd: Cmd::Ping,
            }),
            &mut buf,
        )
        .unwrap()
        .len();
        // Flip a bit in the body.
        buf[HEADER_LEN] ^= 0x01;
        assert!(matches!(decode_from_slice(&buf[..n]), Err(Error::Crc)));
    }
}
