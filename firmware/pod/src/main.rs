//! Phase 4 wing-pod firmware: optional I²C sensors, Ping/Pong, gated Hello,
//! Cmd/Ack (SetRate), dynamic caps, and periodic Status.

#![no_std]
#![no_main]

mod battery_cfg;
mod burst;
mod cfg;
mod cmd;
mod hello;
mod link;
mod power;
mod radio;
mod rates;
mod sensors;

use embassy_executor::Spawner;
use embassy_futures::select::{select, Either};
use embassy_net::{
    udp::{PacketMetadata, UdpSocket},
    IpAddress, IpEndpoint, Runner, Stack, StackResources,
};
use embassy_time::{with_timeout, Duration, Instant, Ticker, Timer};
use esp_alloc as _;
use esp_backtrace as _;
use esp_hal::{
    clock::CpuClock,
    i2c::master::{Config as I2cConfig, I2c},
    interrupt::software::SoftwareInterruptControl,
    rng::Rng,
    rtc_cntl::{sleep::TimerWakeupSource, wakeup_cause, Rtc},
    system::SleepSource,
    time::Rate,
    timer::timg::TimerGroup,
};
use esp_println::println;
use esp_radio::wifi::{
    sta::StationConfig, AuthenticationMethod, Config, ControllerConfig, Interface, PowerSaveMode,
    WifiController,
};
use pod_wire::{Frame, Status};

/// Deep-sleep wake cadence in Protect: long enough to be negligible power,
/// short enough that plugging in a charger is noticed within minutes.
const PROTECT_WAKE_CHECK_S: u64 = 600;

/// Upper bound on any UDP send in uplink_task. A send that cannot complete
/// this fast is pathological — in particular, a send enqueued while the
/// radio is being torn down parks forever on a full socket buffer that
/// nothing will ever drain. That wedged uplink_task (and with it
/// power::tick, which drives every burst/protect transition) for 53 min on
/// 2026-07-15: stuck in BurstCollect, radio off, rings overwriting, until
/// charger current forced Active from the sensor task. No await in
/// uplink_task may block unbounded.
const SEND_TIMEOUT: Duration = Duration::from_millis(250);

esp_bootloader_esp_idf::esp_app_desc!();

macro_rules! mk_static {
    ($t:ty, $val:expr) => {{
        static STATIC_CELL: static_cell::StaticCell<$t> = static_cell::StaticCell::new();
        STATIC_CELL.uninit().write($val)
    }};
}

#[esp_rtos::main]
async fn main(spawner: Spawner) -> ! {
    esp_println::logger::init_logger_from_env();
    let config = esp_hal::Config::default().with_cpu_clock(CpuClock::max());
    let peripherals = esp_hal::init(config);

    esp_alloc::heap_allocator!(size: 72 * 1024);

    let i2c = I2c::new(
        peripherals.I2C0,
        I2cConfig::default().with_frequency(Rate::from_khz(100)),
    )
    .unwrap()
    .with_sda(peripherals.GPIO5)
    .with_scl(peripherals.GPIO6);
    esp_hal::delay::Delay::new().delay_millis(50);
    // Lifetime-launder the I²C bus to 'static for the StaticCell (peripherals
    // are effectively 'static singletons). Types are spelled out so a shape
    // change on either side fails the build; only the lifetimes are inferred.
    let i2c = mk_static!(sensors::bus::Bus, unsafe {
        core::mem::transmute::<esp_hal::i2c::master::I2c<'_, esp_hal::Blocking>, sensors::bus::Bus>(
            i2c,
        )
    });

    let timg0 = TimerGroup::new(peripherals.TIMG0);
    let sw_int = SoftwareInterruptControl::new(peripherals.SW_INTERRUPT);
    esp_rtos::start(timg0.timer0, sw_int.software_interrupt0);

    let mut rtc = Rtc::new(peripherals.LPWR);

    // Protect-mode wake check: after a deep-sleep timer wake, look at the
    // gauge BEFORE bringing up WiFi (the whole point is not to spend the
    // power). Resume boot only when charging or genuinely recovered; if the
    // gauge doesn't answer, fail open and boot normally. No .await happens
    // before this point, so the sensor task cannot race us for the bus.
    if matches!(wakeup_cause(), SleepSource::Timer) {
        match sensors::bq27441::quick_check(i2c) {
            Some((v, i)) if i < 0.05 && v < cfg::BURST_VOLTAGE_UNCALIBRATED => {
                println!(
                    "pod: protect wake check: {:.2} V {:.0} mA, still low; sleeping {} s",
                    v,
                    i * 1000.0,
                    PROTECT_WAKE_CHECK_S
                );
                let timer =
                    TimerWakeupSource::new(core::time::Duration::from_secs(PROTECT_WAKE_CHECK_S));
                rtc.sleep_deep(&[&timer]);
            }
            Some((v, i)) => {
                println!(
                    "pod: protect wake check: {:.2} V {:.0} mA; resuming normal boot",
                    v,
                    i * 1000.0
                );
            }
            None => println!("pod: protect wake check: gauge unreadable; booting normally"),
        }
    }

    spawner.spawn(sensor_bringup_task(i2c).unwrap());

    let station_cfg = if cfg::PASSWORD.is_empty() {
        StationConfig::default()
            .with_ssid(cfg::SSID)
            .with_auth_method(AuthenticationMethod::None)
    } else {
        StationConfig::default()
            .with_ssid(cfg::SSID)
            .with_password(cfg::PASSWORD.into())
    };
    let station = Config::Station(station_cfg);

    println!("pod: starting wifi (ssid={})", cfg::SSID);
    let (controller, interfaces) = esp_radio::wifi::new(
        peripherals.WIFI,
        ControllerConfig::default().with_initial_config(station),
    )
    .unwrap();
    let wifi_interface = interfaces.station;

    let net_config = embassy_net::Config::dhcpv4(Default::default());
    let rng = Rng::new();
    let seed = ((rng.random() as u64) << 32) | (rng.random() as u64);

    let (stack, runner) = embassy_net::new(
        wifi_interface,
        net_config,
        mk_static!(StackResources<3>, StackResources::<3>::new()),
        seed,
    );

    spawner.spawn(connection_task(controller).unwrap());
    spawner.spawn(net_task(runner).unwrap());

    println!("pod: waiting for ip");
    stack.wait_config_up().await;
    if let Some(ipcfg) = stack.config_v4() {
        println!("pod: got ip {}", ipcfg.address);
    }

    spawner.spawn(uplink_task(stack).unwrap());

    loop {
        Timer::after(Duration::from_secs(1)).await;
        if power::mode() == power::PowerMode::Protect {
            // connection_task saw radio_wanted() drop and is stopping the
            // radio; give it a moment for a clean deauth before the lights
            // go out. Deep sleep powers down everything except the RTC.
            Timer::after(Duration::from_secs(2)).await;
            println!(
                "pod: protect: deep sleep, wake check every {} s (reason={:?})",
                PROTECT_WAKE_CHECK_S,
                power::reason()
            );
            let timer =
                TimerWakeupSource::new(core::time::Duration::from_secs(PROTECT_WAKE_CHECK_S));
            rtc.sleep_deep(&[&timer]);
        }
    }
}

#[embassy_executor::task]
async fn sensor_bringup_task(bus: &'static mut sensors::bus::Bus) {
    crate::battery_cfg::init();
    let board = sensors::bringup_board(bus);
    sensors::run_sensor_poll(bus, board).await;
}

fn clamp_rssi_dbm(rssi: i32) -> i8 {
    rssi.clamp(-128, 127) as i8
}

#[embassy_executor::task]
async fn connection_task(mut controller: WifiController<'static>) {
    // Modem power-save is a driver-global setting; esp_wifi_set_ps persists
    // across stop/start cycles, so set it once on the first association.
    let mut power_save_set = false;
    loop {
        if !power::radio_wanted() {
            if controller.is_connected() {
                let _ = controller.disconnect_async().await;
            }
            radio::stop();
            link::clear_wifi_rssi();
            println!("pod: radio off ({:?})", power::mode());
            while !power::radio_wanted() {
                Timer::after(Duration::from_millis(250)).await;
            }
            radio::start();
            println!("pod: radio on ({:?})", power::mode());
            // Let the StationStart event land before connect.
            Timer::after(Duration::from_millis(100)).await;
        }
        match controller.connect_async().await {
            Ok(info) => {
                println!("pod: wifi connected: {:?}", info);
                if cfg::MODEM_POWER_SAVE && !power_save_set {
                    match controller.set_power_saving(PowerSaveMode::Minimum) {
                        Ok(()) => {
                            power_save_set = true;
                            println!("pod: modem power-save Minimum");
                        }
                        Err(e) => println!("pod: set_power_saving: {:?}", e),
                    }
                }
                if let Ok(rssi) = controller.rssi() {
                    link::set_wifi_rssi(clamp_rssi_dbm(rssi));
                }
                let mut poll_tick = Ticker::every(Duration::from_millis(250));
                let mut ticks: u32 = 0;
                loop {
                    match select(poll_tick.next(), controller.wait_for_disconnect_async()).await {
                        Either::First(()) => {
                            if !power::radio_wanted() {
                                break;
                            }
                            ticks = ticks.wrapping_add(1);
                            if ticks % 4 == 0 {
                                match controller.rssi() {
                                    Ok(rssi) => link::set_wifi_rssi(clamp_rssi_dbm(rssi)),
                                    Err(_) => link::clear_wifi_rssi(),
                                }
                            }
                        }
                        Either::Second(Ok(_)) | Either::Second(Err(_)) => {
                            link::clear_wifi_rssi();
                            println!("pod: wifi disconnected");
                            break;
                        }
                    }
                }
            }
            Err(e) => {
                link::clear_wifi_rssi();
                if power::radio_wanted() {
                    println!("pod: wifi connect error: {:?}", e);
                }
            }
        }
        if power::radio_wanted() {
            Timer::after(Duration::from_secs(5)).await;
        }
    }
}

#[embassy_executor::task]
async fn net_task(mut runner: Runner<'static, Interface<'static>>) {
    runner.run().await
}

#[embassy_executor::task]
async fn uplink_task(stack: Stack<'static>) {
    let mut rx_meta = [PacketMetadata::EMPTY; 4];
    let mut rx_buf = [0u8; 256];
    let mut tx_meta = [PacketMetadata::EMPTY; 4];
    let mut tx_buf = [0u8; 1024];
    let mut socket = UdpSocket::new(stack, &mut rx_meta, &mut rx_buf, &mut tx_meta, &mut tx_buf);
    if let Err(e) = socket.bind(0) {
        println!("pod: socket bind: {:?}", e);
        return;
    }

    let dest = IpEndpoint::new(
        IpAddress::v4(cfg::PI_IP[0], cfg::PI_IP[1], cfg::PI_IP[2], cfg::PI_IP[3]),
        cfg::PI_PORT,
    );

    let mut frame_buf = [0u8; 512];
    let mut udp_rx = [0u8; 512];
    let mut ticker = Ticker::every(Duration::from_millis(cfg::TICK_MS));
    let mut next_log = Instant::now() + Duration::from_secs(5);
    let mut next_status = Instant::now() + Duration::from_secs(5);
    let mut sent_since_log: u32 = 0;
    let mut seq: u32 = 0;
    let mut pi_peer: Option<IpEndpoint> = None;
    let mut last_flush_us: u64 = 0;

    loop {
        match select(ticker.next(), socket.recv_from(&mut udp_rx)).await {
            Either::First(()) => {
                let now = Instant::now();
                let uptime_us = now.as_micros();
                power::tick(uptime_us);
                let radio_up = power::radio_wanted() && stack.is_config_up();

                if radio_up && link::should_send_hello(uptime_us) {
                    let mask = sensors::attached_mask();
                    let hello = hello::build(mask);
                    match pod_wire::encode_to_slice(&hello, &mut frame_buf) {
                        Ok(bytes) => {
                            if matches!(
                                with_timeout(SEND_TIMEOUT, socket.send_to(bytes, dest)).await,
                                Ok(Ok(()))
                            ) {
                                link::mark_hello_sent(uptime_us);
                                println!("pod: sent Hello (caps={})", mask);
                            }
                        }
                        Err(_) => println!("pod: hello encode failed"),
                    }
                }

                // Burst-store backlog drains first (oldest data), whenever the
                // link is up: hard in a drain window, opportunistically after
                // a recovery back to Active left leftovers behind.
                let mut drain_clean = true;
                if radio_up && burst::depth() > 0 {
                    const MAX_DRAIN_BATCHES_PER_TICK: u32 = 32;
                    for _ in 0..MAX_DRAIN_BATCHES_PER_TICK {
                        seq = seq.wrapping_add(1);
                        let batch = burst::build_batch(uptime_us, seq);
                        if batch.samples.is_empty() {
                            break;
                        }
                        let frame = Frame::Sample(batch);
                        let mut sent = false;
                        if let Ok(bytes) = pod_wire::encode_to_slice(&frame, &mut frame_buf) {
                            let peer = pi_peer.unwrap_or(dest);
                            sent = matches!(
                                with_timeout(SEND_TIMEOUT, socket.send_to(bytes, peer)).await,
                                Ok(Ok(()))
                            );
                        }
                        if sent {
                            sent_since_log += 1;
                        } else {
                            drain_clean = false;
                            break;
                        }
                    }
                }

                let depth = sensors::pending_depth();
                let flush_due = last_flush_us == 0
                    || uptime_us.saturating_sub(last_flush_us) >= cfg::FLUSH_INTERVAL_US
                    || depth >= cfg::FLUSH_HIGH_WATERMARK
                    || power::drain_requested();
                if radio_up && flush_due && depth > 0 {
                    // Wire batches cap at MAX_READINGS; drain backlog with short bursts.
                    const MAX_BATCHES_PER_FLUSH: u32 = 12;
                    let mut flushed = false;
                    for _ in 0..MAX_BATCHES_PER_FLUSH {
                        seq = seq.wrapping_add(1);
                        let batch = sensors::with_samples_mut(|s| s.build_batch(uptime_us, seq));
                        if batch.samples.is_empty() {
                            break;
                        }
                        let frame = Frame::Sample(batch);
                        if let Ok(bytes) = pod_wire::encode_to_slice(&frame, &mut frame_buf) {
                            let peer = pi_peer.unwrap_or(dest);
                            if matches!(
                                with_timeout(SEND_TIMEOUT, socket.send_to(bytes, peer)).await,
                                Ok(Ok(()))
                            ) {
                                sent_since_log += 1;
                                flushed = true;
                            }
                        }
                        if sensors::pending_depth() == 0 {
                            break;
                        }
                    }
                    if flushed {
                        last_flush_us = uptime_us;
                    }
                }

                // A drain window ends when everything reached the wire; send
                // a fresh Status alongside so the Pi sees the mode + depth 0.
                if power::drain_requested()
                    && radio_up
                    && drain_clean
                    && sensors::buffer_depth() == 0
                {
                    next_status = now; // force immediate Status below
                    match power::mode() {
                        power::PowerMode::BurstUplink => {
                            println!("pod: burst uplink complete");
                            power::note_uplink_complete(uptime_us);
                        }
                        power::PowerMode::ProtectPending => {
                            println!("pod: protect flush complete");
                            power::note_protect_flushed(uptime_us);
                        }
                        _ => {}
                    }
                }

                if radio_up && now >= next_status {
                    next_status = now + Duration::from_secs(5);
                    let status = Frame::Status(Status {
                        pod_uptime_us: uptime_us,
                        battery_v: sensors::latest_battery_v(),
                        rssi_dbm: link::wifi_rssi_dbm(),
                        tx_seq: seq,
                        rx_seq_last: cmd::last_rx_cmd_seq(),
                        power_mode: power::mode() as u8,
                        sleep_reason: power::reason() as u8,
                        buffer_depth: sensors::buffer_depth(),
                        dropped_readings: sensors::dropped_readings()
                            .saturating_add(burst::overwritten()),
                    });
                    if let Ok(bytes) = pod_wire::encode_to_slice(&status, &mut frame_buf) {
                        let peer = pi_peer.unwrap_or(dest);
                        let _ = with_timeout(SEND_TIMEOUT, socket.send_to(bytes, peer)).await;
                    }
                }

                if now >= next_log {
                    if sent_since_log > 0 || power::radio_wanted() {
                        println!("pod: uplink ok, {} pkts in last 5s", sent_since_log);
                    }
                    sent_since_log = 0;
                    next_log = now + Duration::from_secs(5);
                }
            }
            Either::Second(Ok((n, meta))) => {
                if n == 0 {
                    continue;
                }
                let peer = meta.endpoint;
                pi_peer = Some(peer);
                let now_us = Instant::now().as_micros();
                let uptime_us = now_us;
                let replies = cmd::handle_datagram(&udp_rx[..n], now_us, uptime_us);
                // Reply only while the radio is meant to be up: a datagram
                // that raced the burst radio-stop (or sat queued in the
                // socket across a radio-off window) must still be applied,
                // but answering it can wait for the next uplink — this send
                // is what wedged uplink_task on 2026-07-15.
                if power::radio_wanted() && stack.is_config_up() {
                    for reply in replies {
                        if let Frame::Ack(a) = &reply {
                            println!("pod: ack for_seq={} ok={}", a.for_seq, a.ok);
                        }
                        if let Ok(bytes) = pod_wire::encode_to_slice(&reply, &mut frame_buf) {
                            let _ =
                                with_timeout(SEND_TIMEOUT, socket.send_to(bytes, peer)).await;
                        }
                    }
                }
            }
            Either::Second(Err(e)) => {
                println!("pod: udp recv: {:?}", e);
            }
        }
    }
}
