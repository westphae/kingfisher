//! Compile-time pod configuration.
//!
//! SSID, password, and Pi UDP target are injected at build time by
//! `build.rs` from `~/.config/kingfisher/config.json` (see `pod` section).
//! Override the config path with `KINGFISHER_CONFIG`. v2 will use NVS.

pub const SSID: &str = env!("SSID");
pub const PASSWORD: &str = env!("PASSWORD");

/// Pi-side UDP listen address (`host:port`). Must match `pod.udp_addr` in
/// kingfisher config (AP gateway + listen port, usually `47808`).
pub const PI_ADDR: &str = env!("PI_ADDR");

const PI_EP: ([u8; 4], u16) = parse_pi_addr(PI_ADDR.as_bytes());

/// Parsed IPv4 octets from [`PI_ADDR`].
pub const PI_IP: [u8; 4] = PI_EP.0;

/// Parsed UDP port from [`PI_ADDR`].
pub const PI_PORT: u16 = PI_EP.1;

pub const FW_VERSION: u32 = 0x0004_0004;

/// Sensor poll / uplink cadence (Hz). Mag 50 Hz is a later stretch goal.
pub const TICK_MS: u64 = 100;

/// Scheduler rate derived from [`TICK_MS`] (100 ms → 10 Hz).
pub const BASE_HZ: u16 = (1000 / TICK_MS) as u16;

/// Parse `a.b.c.d:port` at compile time (same source as `env!("PI_ADDR")`).
const fn parse_pi_addr(s: &[u8]) -> ([u8; 4], u16) {
    let mut colon = s.len();
    let mut i = 0;
    while i < s.len() {
        if s[i] == b':' {
            colon = i;
            break;
        }
        i += 1;
    }
    if colon == 0 || colon >= s.len() - 1 {
        panic!("PI_ADDR must look like a.b.c.d:port");
    }
    let ip = parse_ipv4(s, 0, colon);
    let port = parse_u16(s, colon + 1, s.len());
    (ip, port)
}

const fn parse_ipv4(s: &[u8], start: usize, end: usize) -> [u8; 4] {
    let mut out = [0u8; 4];
    let mut octet = 0u16;
    let mut idx = 0usize;
    let mut i = start;
    while i < end {
        let b = s[i];
        if b == b'.' {
            if idx >= 3 || octet > 255 {
                panic!("PI_ADDR: invalid IPv4");
            }
            out[idx] = octet as u8;
            idx += 1;
            octet = 0;
        } else if b >= b'0' && b <= b'9' {
            octet = octet * 10 + (b - b'0') as u16;
            if octet > 255 {
                panic!("PI_ADDR: invalid IPv4 octet");
            }
        } else {
            panic!("PI_ADDR: invalid IPv4");
        }
        i += 1;
    }
    if idx != 3 || octet > 255 {
        panic!("PI_ADDR: invalid IPv4");
    }
    out[3] = octet as u8;
    out
}

const fn parse_u16(s: &[u8], start: usize, end: usize) -> u16 {
    if start >= end {
        panic!("PI_ADDR: missing port");
    }
    let mut n = 0u16;
    let mut i = start;
    while i < end {
        let b = s[i];
        if b < b'0' || b > b'9' {
            panic!("PI_ADDR: invalid port");
        }
        n = n * 10 + (b - b'0') as u16;
        i += 1;
    }
    n
}
