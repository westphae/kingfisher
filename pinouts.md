# Kingfisher
A Raspberry Pi-based project to gather sensor data for improving flight performance.

## Main Unit Header Pinout
--------------------------------------
|  ICM45686       | GPS              |
--------------------------------------
|  1 3.3V         |  2 5V            |
|  3 SDA (GPIO 2) |  4 5V            |
|  5 SCL (GPIO 3) |  6 GND           |
|  7 GPIO 4       |  8 TXD (GPIO 14) |
|  9 GND          | 10 RXD (GPIO 15) |
| 11 GPIO 17      | 12 GPIO 18       |
--------------------------------------

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
Other side of board: PPS

### Notes:
* Enable UART0 (GPIO 14/15) on RPI5 with `/boot/firmware/config.txt: dtoverlay=uart0-pi5`
* GPIO 18 (physical pin 12) is the PPS input with `dtoverlay=pps-gpio,gpiopin=18`

## ICM45686 Pinout
-------
| VCC |
| GND |
| SCL |
| SDA |
|     |
|     |
| INT |
------- 

### Notes:
* Supports 8K FIFO buffer (default is 2K, extend to 8K by disabling APEX: SS 2.4)
* FIFO has 20-bit data format for high resolution
* Digital filters for sensors
* 1 MHz I2C
* User-configurable internal pull-up/pull-downs
* User-configurable ODR & FDR (FIFO Data Rate)
* GPIO 17 (physical pin 11) is the INT for the ICM45686 (GPIO 17 is the default in the driver).

## Case
* PLA+ not good for temp, PETG a little better, ASA might be optimal
