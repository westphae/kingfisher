//! Raw WiFi RF power control for burst mode.
//!
//! esp-radio 0.18 exposes no public start/stop — only full deinit on
//! controller Drop, which would tear down the embassy-net interface. Its
//! station state machine is driven by WiFi events (StationStart/StationStop),
//! so calling the IDF driver directly keeps everything coherent: `stop()`
//! fires STA_STOP (state → Stopped, link down), `start()` fires STA_START
//! (state → Started) after which `connect_async` works again.
//!
//! Callers should `disconnect_async().await` before `stop()` for a clean
//! deauth while the RF is still up.

use esp_println::println;
use esp_wifi_sys_esp32c3::include::{esp_wifi_start, esp_wifi_stop};

/// Power down the RF/modem entirely (the big burst-mode saving).
pub fn stop() {
    let err = unsafe { esp_wifi_stop() };
    if err != 0 {
        println!("pod: esp_wifi_stop err={}", err);
    }
}

/// Power the modem back up; follow with `connect_async`.
pub fn start() {
    let err = unsafe { esp_wifi_start() };
    if err != 0 {
        println!("pod: esp_wifi_start err={}", err);
    }
}
