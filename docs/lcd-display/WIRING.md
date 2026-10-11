# Parts and wiring

## Parts
- ESP32 dev board (ESP8266 works too), connected to the laptop by its own USB cable. This is a **second** USB device, separate from the servo controller.
- Display, pick one:
  - **Standard 1602 + PCF8574 I2C backpack**: single-colour backlight, so no red/yellow/green. Set `LCD_RGB 0`.
  - **Grove LCD RGB Backlight 16x2 (I2C)**: needed for the red / yellow / green backlight. Set `LCD_RGB 1`.

## Wiring (ESP32 → LCD)
| LCD pin | ESP32 |
|---|---|
| GND | GND |
| VCC | 5V (VIN) |
| SDA | GPIO 21 |
| SCL | GPIO 22 |

### ESP32-S3 (HW678 / S3-N16R8, the board on the bench)
GPIO 22 does not exist on the S3; `Wire.begin()` uses the S3 defaults instead.

| LCD pin | ESP32-S3 (HW678) |
|---|---|
| GND | GND |
| VCC | **5Vin** (second pin from the bottom on the GPIO 4–14 side) |
| SDA | GPIO 8 |
| SCL | GPIO 9 |

- On the HW678, `5Vin` only has 5 V when the USB cable is in the **COM** port, or when the **IN-OUT** solder pads are bridged. Using only the native **USB** port, the LCD stays dark. On 3.3 V the backlight lights but the text is unreadable.
- The plain 1602 on the bench has no contrast pot; its contrast is fixed by resistors. If the text is faint on 5 V, add a 1–2.2 kΩ resistor between LCD holes 1 (VSS) and 3 (VO).
- The onboard RGB LED (GPIO 48) mirrors the backlight colours from SPEC.md (`STATUS_LED_PIN` in the sketch). On some HW678 batches it only works after bridging the small **RGB** solder jumper.
- The COM port enumerates with the same USB chip type (CH343, `1a86:55d3`) as the servo controller. Set `HANDOFF_SERVO_PORT` to the servo controller's port (e.g. in `backend/.env`) so status checks never confuse the two.

Most 1602 backpacks pull SDA/SCL up to 5 V. ESP32 pins are 3.3 V, so a bidirectional level shifter on SDA/SCL is the safe choice. It often works without one, but that isn't guaranteed. On ESP8266 use D2 for SDA and D1 for SCL.

## Flash
1. Arduino IDE: install the ESP32 board package, plus the library for your display ("LiquidCrystal I2C" by Frank de Brabander, or "Grove - LCD RGB Backlight").
   For the HW678: board **ESP32S3 Dev Module**, **USB CDC On Boot: Disabled** (so `Serial` is the COM port), **Flash Size: 16MB**, and the COM port as the upload port. If the upload cannot connect, hold BOOT, tap RST, release BOOT, and upload again.
2. Open `firmware/handoff_lcd/handoff_lcd.ino`, set `LCD_RGB` (and `LCD_ADDR` 0x27 or 0x3F for the 1602), then upload.
3. Without the backend running, the screen shows `NO DATA FROM APP / CHECK USB CABLE`. That's correct.

## Run with the backend
```bash
ls /dev/cu.*        # find the ESP, e.g. /dev/cu.usbserial-0001 or /dev/cu.SLAB_USBtoUART
cd backend
HANDOFF_LCD_PORT=/dev/cu.usbserial-0001 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Close the Arduino Serial Monitor first, because only one program can hold the port. Make sure `HANDOFF_LCD_PORT` is the ESP, **not** the servo controller.
