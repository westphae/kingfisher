//! Reads kingfisher config.json at build time and injects pod WiFi/UDP settings
//! into the firmware via cargo:rustc-env (consumed by src/cfg.rs env!).

use std::env;
use std::fs;
use std::path::PathBuf;

use serde::Deserialize;

const DEFAULT_UDP: &str = "192.168.10.1:47808";

#[derive(Debug, Deserialize, Default)]
struct FileConfig {
    #[serde(default)]
    pod: PodSection,
    #[serde(default, rename = "pod_udp_addr")]
    pod_udp_addr: String,
}

#[derive(Debug, Deserialize, Default)]
struct PodSection {
    #[serde(default, rename = "wifi_ssid")]
    wifi_ssid: String,
    #[serde(default, rename = "wifi_password")]
    wifi_password: String,
    #[serde(default, rename = "udp_addr")]
    udp_addr: String,
}

fn main() {
    let config_path = resolve_config_path();
    let raw = fs::read_to_string(&config_path).unwrap_or_else(|e| {
        panic!(
            "pod firmware build: read config {}: {e}\n\
             Set KINGFISHER_CONFIG to your kingfisher config.json path.",
            config_path.display()
        );
    });

    let cfg: FileConfig = serde_json::from_str(&raw).unwrap_or_else(|e| {
        panic!(
            "pod firmware build: parse config {}: {e}",
            config_path.display()
        );
    });

    let mut udp_addr = cfg.pod.udp_addr;
    if udp_addr.is_empty() {
        udp_addr = cfg.pod_udp_addr;
    }
    if udp_addr.is_empty() {
        udp_addr = DEFAULT_UDP.to_string();
    }

    let ssid = cfg.pod.wifi_ssid;
    if ssid.is_empty() {
        panic!(
            "pod firmware build: config {} missing pod.wifi_ssid\n\
             Add a \"pod\" section with wifi_ssid, wifi_password, and udp_addr.",
            config_path.display()
        );
    }

    validate_no_newlines("pod.wifi_ssid", &ssid);
    validate_no_newlines("pod.wifi_password", &cfg.pod.wifi_password);
    validate_udp_addr(&udp_addr);

    println!("cargo:rerun-if-changed={}", config_path.display());
    println!("cargo:rerun-if-env-changed=KINGFISHER_CONFIG");
    println!("cargo:rustc-env=SSID={ssid}");
    println!("cargo:rustc-env=PASSWORD={}", cfg.pod.wifi_password);
    println!("cargo:rustc-env=PI_ADDR={udp_addr}");
}

fn resolve_config_path() -> PathBuf {
    if let Ok(p) = env::var("KINGFISHER_CONFIG") {
        return PathBuf::from(p);
    }
    let home = env::var("HOME").unwrap_or_else(|_| {
        panic!(
            "pod firmware build: HOME not set; export KINGFISHER_CONFIG=/path/to/config.json"
        );
    });
    PathBuf::from(home)
        .join(".config")
        .join("kingfisher")
        .join("config.json")
}

fn validate_no_newlines(field: &str, value: &str) {
    if value.contains('\n') || value.contains('\r') {
        panic!("pod firmware build: {field} must not contain newlines");
    }
}

fn validate_udp_addr(addr: &str) {
    let Some((host, port)) = addr.rsplit_once(':') else {
        panic!("pod firmware build: pod.udp_addr must be host:port, got {addr:?}");
    };
    if host.is_empty() || port.is_empty() {
        panic!("pod firmware build: pod.udp_addr must be host:port, got {addr:?}");
    }
    if !port.bytes().all(|b| b.is_ascii_digit()) {
        panic!("pod firmware build: invalid port in pod.udp_addr {addr:?}");
    }
    let parts: Vec<_> = host.split('.').collect();
    if parts.len() != 4 {
        panic!("pod firmware build: pod.udp_addr host must be IPv4, got {addr:?}");
    }
}
