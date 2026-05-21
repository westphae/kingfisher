//! Compile-time pod configuration.
//!
//! WiFi credentials come from build-time env vars (so they don't sit in
//! version control). The Pi IP/port is hardcoded here — edit before
//! flashing if it changes. v2 will move all of this to NVS with a
//! provisioning flow.
//!
//! Build with:
//!   SSID=kingfisher-ap PASSWORD=change-me cargo build --release
//!
//! Defaults in `.cargo/config.toml` are placeholders so the build
//! succeeds even when the env vars aren't explicitly set.

pub const SSID: &str = env!("SSID");
pub const PASSWORD: &str = env!("PASSWORD");

/// Pi-side UDP listen address. Must match `pod_udp_addr` in kingfisher
/// config (default `:47808`).
pub const PI_IP: [u8; 4] = [192, 168, 4, 1];
pub const PI_PORT: u16 = 47808;

pub const FW_VERSION: u32 = 0x0001_0000;

/// Phase 1 cadence — single 10 Hz tick covers every Reading. Per-sensor
/// rates land in Phase 3 along with the real sensors.
pub const TICK_MS: u64 = 100;
