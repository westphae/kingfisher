//! Emit hex-encoded reference frames for cross-language test fixtures.
//!
//! Each output line is `<name>\t<hex>` — consumed by the Go decoder test
//! in `internal/pod/wire/decode_test.go`.

use pod_wire::*;

fn dump(name: &str, frame: &Frame) {
    let mut buf = [0u8; 512];
    let bytes = encode_to_slice(frame, &mut buf).expect("encode");
    println!("{}\t{}", name, hex::encode(bytes));
}

fn main() {
    // Hello with two sensors advertised.
    let mut sensors = heapless::Vec::<SensorCap, MAX_SENSORS>::new();
    sensors
        .push(SensorCap {
            id: SensorId::Airspeed,
            min_hz: 1,
            max_hz: 50,
            default_hz: 10,
            device_name: SensorDeviceName::from_str("ms4525"),
        })
        .unwrap();
    sensors
        .push(SensorCap {
            id: SensorId::Mag,
            min_hz: 1,
            max_hz: 200,
            default_hz: 50,
            device_name: SensorDeviceName::from_str("mmc5983"),
        })
        .unwrap();
    dump(
        "hello",
        &Frame::Hello(Hello {
            fw_version: 0x00010203,
            proto_version: PROTO_VERSION,
            caps: Capabilities { sensors },
        }),
    );

    // Sample batch with one of each reading type.
    let mut samples = heapless::Vec::<Reading, MAX_READINGS>::new();
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
    dump(
        "sample_batch",
        &Frame::Sample(SampleBatch {
            pod_uptime_us: 1_234_567_890,
            seq: 42,
            samples,
        }),
    );

    dump(
        "cmd_set_rate",
        &Frame::Cmd(CmdEnvelope {
            seq: 1,
            cmd: Cmd::SetRate {
                sensor: SensorId::Mag,
                hz: 50,
            },
        }),
    );
    dump(
        "cmd_set_attr",
        &Frame::Cmd(CmdEnvelope {
            seq: 2,
            cmd: Cmd::SetAttr {
                sensor: SensorId::Static,
                key: AttrKey::Oversampling,
                value: 16.0,
            },
        }),
    );
    dump(
        "cmd_ping",
        &Frame::Cmd(CmdEnvelope {
            seq: 3,
            cmd: Cmd::Ping,
        }),
    );
    dump(
        "cmd_reboot",
        &Frame::Cmd(CmdEnvelope {
            seq: 4,
            cmd: Cmd::Reboot,
        }),
    );

    dump(
        "status",
        &Frame::Status(Status {
            pod_uptime_us: 5_000_000,
            battery_v: 3.78,
            rssi_dbm: -64,
            tx_seq: 100,
            rx_seq_last: 12,
        }),
    );

    dump(
        "ping",
        &Frame::Ping(Ping {
            seq: 7,
            sender_uptime_us: 999,
        }),
    );
    dump(
        "pong",
        &Frame::Pong(Pong {
            seq: 7,
            sender_uptime_us: 1100,
            echo_uptime_us: 999,
        }),
    );

    dump(
        "ack_ok",
        &Frame::Ack(Ack {
            for_seq: 99,
            ok: true,
        }),
    );
    dump(
        "ack_fail",
        &Frame::Ack(Ack {
            for_seq: 100,
            ok: false,
        }),
    );
}
