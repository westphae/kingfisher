//! Link keepalive: inbound activity tracking and Hello gating.

use core::cell::RefCell;
use core::sync::atomic::{AtomicBool, AtomicI8, Ordering};

use critical_section::Mutex;

/// Re-send Hello if the link looks dead for this long (µs).
pub const HELLO_REDISCOVER_US: u64 = 30_000_000;

static LAST_INBOUND_US: Mutex<RefCell<u64>> = Mutex::new(RefCell::new(0));
static LAST_HELLO_SENT_US: Mutex<RefCell<u64>> = Mutex::new(RefCell::new(0));
static HELLO_PENDING: AtomicBool = AtomicBool::new(true);
/// Latest STA RSSI toward the Pi AP (dBm), updated by the WiFi task. 0 = unknown.
static WIFI_RSSI_DBM: AtomicI8 = AtomicI8::new(0);

pub fn set_wifi_rssi(dbm: i8) {
    WIFI_RSSI_DBM.store(dbm, Ordering::Relaxed);
}

pub fn clear_wifi_rssi() {
    WIFI_RSSI_DBM.store(0, Ordering::Relaxed);
}

/// Last STA RSSI (dBm) from the WiFi task; 0 if unknown / disconnected.
pub fn wifi_rssi_dbm() -> i8 {
    WIFI_RSSI_DBM.load(Ordering::Relaxed)
}

pub fn touch_inbound(now_us: u64) {
    critical_section::with(|cs| {
        *LAST_INBOUND_US.borrow(cs).borrow_mut() = now_us;
    });
}

pub fn request_hello() {
    HELLO_PENDING.store(true, Ordering::Relaxed);
}

pub fn mark_hello_sent(now_us: u64) {
    critical_section::with(|cs| {
        *LAST_HELLO_SENT_US.borrow(cs).borrow_mut() = now_us;
    });
    HELLO_PENDING.store(false, Ordering::Relaxed);
}

fn take_hello_pending() -> bool {
    HELLO_PENDING.load(Ordering::Relaxed)
}

/// Boot/caps-change (`request_hello`), or ~30 s since last Hello while the Pi
/// is not talking to us.
pub fn should_send_hello(now_us: u64) -> bool {
    if take_hello_pending() {
        return true;
    }
    let last_in = critical_section::with(|cs| *LAST_INBOUND_US.borrow(cs).borrow());
    if last_in != 0 && now_us.saturating_sub(last_in) <= HELLO_REDISCOVER_US {
        return false;
    }
    let last_hello = critical_section::with(|cs| *LAST_HELLO_SENT_US.borrow(cs).borrow());
    now_us.saturating_sub(last_hello) > HELLO_REDISCOVER_US
}
