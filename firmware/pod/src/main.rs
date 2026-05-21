//! Phase 3b wing-pod firmware: WiFi + BMP581 static + MMC5983MA mag over I²C.
//! Airspeed (MS4525DO) lands in Phase 3c.

#![no_std]
#![no_main]

mod cfg;
mod sensors;

use embassy_executor::Spawner;
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
    // SparkFun Pro Micro ESP32-C3 Qwiic: SDA=GPIO5, SCL=GPIO6 (pins_arduino.h).
    // Wing pod PCB target uses GPIO4/GPIO5 — change when that board exists.
    .with_sda(peripherals.GPIO5)
    .with_scl(peripherals.GPIO6);
    esp_hal::delay::Delay::new().delay_millis(50);
    // SAFETY: unique ownership of I2C/pin peripherals for the life of the firmware.
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

/// Probe sensors on the shared bus; retry every 2s until init succeeds, then poll forever.
#[embassy_executor::task]
async fn sensor_bringup_task(bus: &'static mut sensors::bus::Bus) {
    let board = loop {
        if let Some(board) = sensors::bringup_board(bus) {
            break board;
        }
        println!("pod: sensor board init failed; retry in 2s");
        embassy_time::Timer::after(embassy_time::Duration::from_secs(2)).await;
    };
    sensors::run_sensor_poll(bus, board).await;
}

#[embassy_executor::task]
async fn connection_task(mut controller: WifiController<'static>) {
    loop {
        match controller.connect_async().await {
            Ok(info) => {
                println!("pod: wifi connected: {:?}", info);
                let _ = controller.wait_for_disconnect_async().await;
                println!("pod: wifi disconnected");
            }
            Err(e) => {
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

    let hello = build_hello();
    let mut next_hello = Instant::now();
    let mut ticker = Ticker::every(Duration::from_millis(cfg::TICK_MS));
    let mut next_log = Instant::now() + Duration::from_secs(5);
    let mut sent_since_log: u32 = 0;
    let mut seq: u32 = 0;
    loop {
        ticker.next().await;
        let now = Instant::now();
        if now >= next_hello {
            next_hello = now + Duration::from_secs(5);
            match pod_wire::encode_to_slice(&hello, &mut frame_buf) {
                Ok(bytes) => match socket.send_to(bytes, dest).await {
                    Ok(()) => println!("pod: sent Hello -> {}", dest),
                    Err(e) => println!("pod: hello send: {:?}", e),
                },
                Err(_) => println!("pod: hello encode failed"),
            }
        }
        let uptime_us = Instant::now().as_micros();
        seq = seq.wrapping_add(1);
        let batch = sensors::with_samples(|s| s.build_batch(uptime_us, seq));
        if batch.samples.is_empty() {
            continue;
        }
        let frame = pod_wire::Frame::Sample(batch);
        let bytes = match pod_wire::encode_to_slice(&frame, &mut frame_buf) {
            Ok(b) => b,
            Err(_) => {
                println!("pod: encode failed");
                continue;
            }
        };
        if let Err(e) = socket.send_to(bytes, dest).await {
            println!("pod: send: {:?}", e);
            continue;
        }
        sent_since_log += 1;
        if now >= next_log {
            println!("pod: uplink ok, {} pkts in last 5s", sent_since_log);
            sent_since_log = 0;
            next_log = now + Duration::from_secs(5);
        }
    }
}

fn build_hello() -> pod_wire::Frame {
    use heapless::Vec;
    use pod_wire::*;
    let mut sensors: Vec<SensorCap, MAX_SENSORS> = Vec::new();
    let _ = sensors.push(SensorCap {
        id: SensorId::Airspeed,
        min_hz: 1,
        max_hz: 50,
        default_hz: 10,
    });
    let _ = sensors.push(SensorCap {
        id: SensorId::Static,
        min_hz: 1,
        max_hz: 50,
        default_hz: 10,
    });
    let _ = sensors.push(SensorCap {
        id: SensorId::Mag,
        min_hz: 1,
        max_hz: 200,
        default_hz: 50,
    });
    Frame::Hello(Hello {
        fw_version: cfg::FW_VERSION,
        proto_version: PROTO_VERSION,
        caps: Capabilities { sensors },
    })
}
