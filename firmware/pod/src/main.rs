//! Phase 4 wing-pod firmware: optional I²C sensors, Ping/Pong, gated Hello,
//! Cmd/Ack (SetRate), dynamic caps, and periodic Status.

#![no_std]
#![no_main]

mod cfg;
mod cmd;
mod hello;
mod link;
mod rates;
mod sensors;

use embassy_executor::Spawner;
use embassy_futures::select::{select, Either};
use embassy_net::{
    udp::{PacketMetadata, UdpSocket},
    IpAddress, IpEndpoint, Runner, Stack, StackResources,
};
use embassy_time::{Duration, Instant, Ticker, Timer};
use esp_alloc as _;
use esp_backtrace as _;
use esp_hal::{
    clock::CpuClock,
    i2c::master::{Config as I2cConfig, I2c},
    interrupt::software::SoftwareInterruptControl,
    rng::Rng,
    time::Rate,
    timer::timg::TimerGroup,
};
use esp_println::println;
use esp_radio::wifi::{
    sta::StationConfig, AuthenticationMethod, Config, ControllerConfig, Interface,
    WifiController,
};
use pod_wire::{Frame, Status};

esp_bootloader_esp_idf::esp_app_desc!();

macro_rules! mk_static {
    ($t:ty, $val:expr) => {{
        static STATIC_CELL: static_cell::StaticCell<$t> = static_cell::StaticCell::new();
        STATIC_CELL.uninit().write(($val))
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
    let i2c = mk_static!(sensors::bus::Bus, unsafe { core::mem::transmute(i2c) });
    spawner.spawn(sensor_bringup_task(i2c).unwrap());

    let timg0 = TimerGroup::new(peripherals.TIMG0);
    let sw_int = SoftwareInterruptControl::new(peripherals.SW_INTERRUPT);
    esp_rtos::start(timg0.timer0, sw_int.software_interrupt0);

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
        Timer::after(Duration::from_secs(60)).await;
    }
}

#[embassy_executor::task]
async fn sensor_bringup_task(bus: &'static mut sensors::bus::Bus) {
    let board = sensors::bringup_board(bus);
    sensors::run_sensor_poll(bus, board).await;
}

fn clamp_rssi_dbm(rssi: i32) -> i8 {
    rssi.clamp(-128, 127) as i8
}

#[embassy_executor::task]
async fn connection_task(mut controller: WifiController<'static>) {
    let mut rssi_tick = Ticker::every(Duration::from_secs(1));
    loop {
        match controller.connect_async().await {
            Ok(info) => {
                println!("pod: wifi connected: {:?}", info);
                if let Ok(rssi) = controller.rssi() {
                    link::set_wifi_rssi(clamp_rssi_dbm(rssi));
                }
                loop {
                    match select(rssi_tick.next(), controller.wait_for_disconnect_async()).await {
                        Either::First(()) => match controller.rssi() {
                            Ok(rssi) => link::set_wifi_rssi(clamp_rssi_dbm(rssi)),
                            Err(_) => link::clear_wifi_rssi(),
                        },
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
                println!("pod: wifi connect error: {:?}", e);
            }
        }
        Timer::after(Duration::from_secs(5)).await;
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
    let mut socket = UdpSocket::new(
        stack,
        &mut rx_meta,
        &mut rx_buf,
        &mut tx_meta,
        &mut tx_buf,
    );
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

    loop {
        match select(ticker.next(), socket.recv_from(&mut udp_rx)).await {
            Either::First(()) => {
                let now = Instant::now();
                let uptime_us = now.as_micros();

                if link::should_send_hello(uptime_us) {
                    let mask = sensors::attached_mask();
                    let hello = hello::build(mask);
                    match pod_wire::encode_to_slice(&hello, &mut frame_buf) {
                        Ok(bytes) => {
                            if socket.send_to(bytes, dest).await.is_ok() {
                                link::mark_hello_sent(uptime_us);
                                println!("pod: sent Hello (caps={})", mask);
                            }
                        }
                        Err(_) => println!("pod: hello encode failed"),
                    }
                }

                seq = seq.wrapping_add(1);
                let batch = sensors::with_samples_mut(|s| s.build_batch(uptime_us, seq));
                if !batch.samples.is_empty() {
                    let frame = Frame::Sample(batch);
                    if let Ok(bytes) = pod_wire::encode_to_slice(&frame, &mut frame_buf) {
                        let peer = pi_peer.unwrap_or(dest);
                        if socket.send_to(bytes, peer).await.is_ok() {
                            sent_since_log += 1;
                        }
                    }
                }

                if now >= next_status {
                    next_status = now + Duration::from_secs(5);
                    let status = Frame::Status(Status {
                        pod_uptime_us: uptime_us,
                        battery_v: 0.0,
                        rssi_dbm: link::wifi_rssi_dbm(),
                        tx_seq: seq,
                        rx_seq_last: cmd::last_rx_cmd_seq(),
                    });
                    if let Ok(bytes) = pod_wire::encode_to_slice(&status, &mut frame_buf) {
                        let peer = pi_peer.unwrap_or(dest);
                        let _ = socket.send_to(bytes, peer).await;
                    }
                }

                if now >= next_log {
                    println!("pod: uplink ok, {} pkts in last 5s", sent_since_log);
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
                for reply in replies {
                    match &reply {
                        Frame::Ack(a) => {
                            println!("pod: ack for_seq={} ok={}", a.for_seq, a.ok);
                        }
                        _ => {}
                    }
                    if let Ok(bytes) = pod_wire::encode_to_slice(&reply, &mut frame_buf) {
                        let _ = socket.send_to(bytes, peer).await;
                    }
                }
            }
            Either::Second(Err(e)) => {
                println!("pod: udp recv: {:?}", e);
            }
        }
    }
}
