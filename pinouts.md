# Kingfisher
A Raspberry Pi-based project to gather sensor data for improving flight performance.

## Main Unit Header Pinout
-------------------------------------
| ICM45686       | GPS              |
-------------------------------------
| 1 3.3V         | 2  5V            |
| 3 SDA (GPIO 2) | 4  5V            |
| 5 SCL (GPIO 3) | 6  GND           |
| 7              | 8  TXD (GPIO 14) |
| 9 GND          | 10 RXD (GPIO 15) |
-------------------------------------

## GPS Pinout
-------
| GND |
| 5V  |
|     |
|     |
| RXD |
| TXD |
|     |
-------

### Notes:
* Enable UART0 (GPIO 14/15) on RPI5 with `/boot/firmware/config.txt: dtoverlay=uart0-pi5`

## ICM45686 Pinout
-------
| VCC |
| GND |
| SCL |
| SDA |
------- 

### Notes:
* Supports 8K FIFO buffer (default is 2K, extend to 8K by disabling APEX: SS 2.4)
* FIFO has 20-bit data format for high resolution
* Digital filters for sensors
* 1 MHz I2C
* User-configurable internal pull-up/pull-downs
* User-configurable ODR & FDR (FIFO Data Rate)
