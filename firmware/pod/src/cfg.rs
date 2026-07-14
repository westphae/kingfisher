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

// 0x0004_0006: three-stage power protocol (burst/protect), Ping keepalive
// fix, MMC5983 spread-poll harvesting at configured rate.
pub const FW_VERSION: u32 = 0x0004_0006;

/// Sensor poll / uplink cadence (Hz). Mag 50 Hz is a later stretch goal.
pub const TICK_MS: u64 = 100;

/// Scheduler rate derived from [`TICK_MS`] (100 ms → 10 Hz).
pub const BASE_HZ: u16 = (1000 / TICK_MS) as u16;

/// LiPo design capacity (mAh) for BQ27441 gauge configuration at build time.
pub const BATTERY_CAPACITY_MAH: u16 = parse_env_u16(env!("BATTERY_CAPACITY_MAH"));

// Three-stage power protocol thresholds (see power.rs).
pub const BURST_SOC_PCT: u8 = parse_env_u8(env!("BURST_SOC_PCT"));
pub const BURST_WINDOW_S: usize = parse_env_u16(env!("BURST_WINDOW_S")) as usize;
pub const BURST_WINDOW_US: u64 = BURST_WINDOW_S as u64 * 1_000_000;
pub const BURST_VOLTAGE_UNCALIBRATED: f32 = parse_env_millivolts(env!("BURST_VOLTAGE_UNCAL"));
pub const PROTECT_VOLTAGE: f32 = parse_env_millivolts(env!("PROTECT_VOLTAGE"));
pub const PROTECT_SOC_PCT: u8 = parse_env_u8(env!("PROTECT_SOC_PCT"));
pub const LOW_DEBOUNCE_US: u64 = parse_env_u16(env!("LOW_DEBOUNCE_S")) as u64 * 1_000_000;

/// Modem power-save (DTIM doze) while associated. Default off: the Pi's
/// brcmfmac AP fails to deliver buffered unicast to a dozing STA (verified
/// 2026-07-14), which silently kills the Cmd/Ping inbound path.
pub const MODEM_POWER_SAVE: bool = matches!(env!("MODEM_POWER_SAVE").as_bytes(), b"1");
pub const FLUSH_INTERVAL_US: u64 = parse_env_u16(env!("FLUSH_INTERVAL_S")) as u64 * 1_000_000;
pub const FLUSH_HIGH_WATERMARK: u16 = parse_env_u16(env!("FLUSH_HIGH_WATERMARK"));
pub const BUFFER_MAX_READINGS: u16 = parse_env_u16(env!("BUFFER_MAX_READINGS"));

/// Parse decimal u16 at compile time from env string.
const fn parse_env_u16(s: &str) -> u16 {
    let bytes = s.as_bytes();
    let mut i = 0usize;
    let mut n = 0u16;
    while i < bytes.len() {
        let b = bytes[i];
        if b < b'0' || b > b'9' {
            panic!("BATTERY_CAPACITY_MAH must be decimal");
        }
        n = n * 10 + (b - b'0') as u16;
        i += 1;
    }
    if n == 0 {
        panic!("BATTERY_CAPACITY_MAH must be > 0");
    }
    n
}

const fn parse_env_u8(s: &str) -> u8 {
    let n = parse_env_u16(s);
    if n > u8::MAX as u16 {
        panic!("u8 env parse overflow");
    }
    n as u8
}

// Parse x.yy volts into f32 at compile time via integer millivolts.
const fn parse_env_millivolts(s: &str) -> f32 {
    let bytes = s.as_bytes();
    let mut i = 0usize;
    let mut whole = 0u16;
    let mut frac = 0u16;
    let mut frac_digits = 0u8;
    let mut seen_dot = false;
    while i < bytes.len() {
        let b = bytes[i];
        if b == b'.' {
            if seen_dot {
                panic!("invalid float env");
            }
            seen_dot = true;
        } else if b >= b'0' && b <= b'9' {
            if !seen_dot {
                whole = whole * 10 + (b - b'0') as u16;
            } else if frac_digits < 3 {
                frac = frac * 10 + (b - b'0') as u16;
                frac_digits += 1;
            }
        } else {
            panic!("invalid float env");
        }
        i += 1;
    }
    while frac_digits < 3 {
        frac *= 10;
        frac_digits += 1;
    }
    let mv = whole as u32 * 1000 + frac as u32;
    mv as f32 / 1000.0
}

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
