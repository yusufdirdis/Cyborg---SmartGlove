// handoff · torque readiness on a 16x2 LCD (display only — this sketch never sends a command).
// Board: ESP32 (ESP8266 also works). Spec: docs/lcd-display/SPEC.md, protocol: PROTOCOL.md.
//
// LCD_RGB 0 → standard 1602 with a PCF8574 I2C backpack. Library: "LiquidCrystal I2C" (Frank de Brabander). Address usually 0x27 (sometimes 0x3F).
// LCD_RGB 1 → Grove LCD RGB Backlight 16x2 (needed for red / yellow / green). Library: "Grove - LCD RGB Backlight" (rgb_lcd).
#define LCD_RGB 0
#define LCD_ADDR 0x27
// Onboard RGB status LED that mirrors the backlight colours (HW678 / ESP32-S3-DevKitC: GPIO 48). -1 disables it.
#define STATUS_LED_PIN 48
#define STATUS_LED_DIVISOR 6  // the LED is very bright at full power
// I2C uses the board's default pins via Wire.begin(): ESP32-S3 SDA 8 / SCL 9, classic ESP32 SDA 21 / SCL 22.
#define BAUD 115200
#define NODATA_MS 5000

#include <Wire.h>
#if LCD_RGB
#include <rgb_lcd.h>
rgb_lcd lcd;
#else
#include <LiquidCrystal_I2C.h>
LiquidCrystal_I2C lcd(LCD_ADDR, 16, 2);
#endif

enum State { NODATA, STOPPED, LOCKED, OFFLINE, UNKNOWN, ALIGNING, NOT_ENABLED, TORQUE_ON, READY, SETTLING };
State state = NODATA, shown = (State)-1;
int arg = 0, shownArg = -1;
unsigned long lastMsg = 0, lastFrame = 0;
uint8_t frame = 0;
String inbox;

const uint8_t FIXED[4][8] = {
  {0, 0, 0, 0x1F, 0x1F, 0, 0, 0},       // 0 done
  {0, 0, 0, 0, 0x15, 0, 0, 0},          // 1 to go
  {0, 0, 4, 0x1F, 0x1F, 4, 4, 0},       // 2 done + tick
  {0, 0, 4, 4, 0x15, 4, 4, 0},          // 3 to go + tick
};
const char* GLOVE[8] = {"...XXX....", "..XXXXXXX.", "X.XXXXXXXX", "X.XXXX....", "X.XXXXX...", "X.XXXX....", "X.XXXXX...", "..XXXX...."};

void backlight(uint8_t r, uint8_t g, uint8_t b) {
#if LCD_RGB
  lcd.setRGB(r, g, b);
#endif
#if defined(ESP32) && STATUS_LED_PIN >= 0
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  rgbLedWrite(STATUS_LED_PIN, r / STATUS_LED_DIVISOR, g / STATUS_LED_DIVISOR, b / STATUS_LED_DIVISOR);
#else
  neopixelWrite(STATUS_LED_PIN, r / STATUS_LED_DIVISOR, g / STATUS_LED_DIVISOR, b / STATUS_LED_DIVISOR);
#endif
#endif
}

void printLine(uint8_t row, const String& text) {
  String t = text; t.toUpperCase();
  while (t.length() < 16) t += ' ';
  lcd.setCursor(0, row);
  lcd.print(t.substring(0, 16));
}

// Draws the 80 x 8 px progress bar with the glove marker on line 2.
void drawBar(int pct, int nudge) {
  static uint8_t px[8][80];
  memset(px, 0, sizeof(px));
  int tip = 9 + (pct * 70 + 50) / 100;
  int left = constrain(tip - 9 + nudge, 0, 70);
  for (int x = 0; x < 80; x++) {
    if (x >= left && x < left + 10) continue;
    if (x <= left - 2) { px[3][x] = 1; px[4][x] = 1; }
    else if (x >= left + 10 && (x % 5) % 2 == 0) px[4][x] = 1;
    if (x == 27 || x == 52) for (int r = 2; r <= 6; r++) px[r][x] = 1;
  }
  for (int r = 0; r < 8; r++) for (int j = 0; j < 10; j++) if (GLOVE[r][j] == 'X') px[r][left + j] = 1;

  uint8_t slots[16], dyn[3][8]; int dynCount = 0;
  for (int c = 0; c < 16; c++) {
    uint8_t rows[8];
    for (int r = 0; r < 8; r++) { uint8_t b = 0; for (int k = 0; k < 5; k++) b = (b << 1) | px[r][c * 5 + k]; rows[r] = b; }
    int slot = -1;
    for (int f = 0; f < 4 && slot < 0; f++) if (memcmp(rows, FIXED[f], 8) == 0) slot = f;
    if (slot < 0 && dynCount < 3) { memcpy(dyn[dynCount], rows, 8); slot = 4 + dynCount++; }
    slots[c] = slot < 0 ? 1 : slot;
  }
  for (int i = 0; i < dynCount; i++) lcd.createChar(4 + i, dyn[i]);
  lcd.setCursor(0, 1);
  for (int c = 0; c < 16; c++) lcd.write(slots[c]);
}

void render() {
  switch (state) {
    case NODATA:      backlight(35, 70, 200); printLine(0, "NO DATA FROM APP"); printLine(1, "CHECK USB CABLE"); break;
    case STOPPED:     backlight(35, 70, 200); printLine(0, "STOPPED"); printLine(1, "RESET ON WEBSITE"); break;
    case LOCKED:      backlight(35, 70, 200); printLine(0, "CONTROL LOCKED"); printLine(1, "ENABLE ON SERVER"); break;
    case OFFLINE:     backlight(35, 70, 200); printLine(0, "SERVOS OFFLINE"); printLine(1, "CHECK USB+POWER"); break;
    case UNKNOWN:     backlight(35, 70, 200); printLine(0, "GLOVE UNKNOWN"); printLine(1, "OPEN THE WEBSITE"); break;
    case NOT_ENABLED: backlight(35, 70, 200); printLine(0, "NOT ENABLED"); printLine(1, arg > 0 ? "S" + String(arg) + " MOVING - WAIT" : "SEE WEBSITE"); break;
    case ALIGNING:    backlight(40, 200, 70); printLine(0, String("ALIGNING") + String("...").substring(0, frame % 3 + 1)); drawBar(83, frame % 2 ? 1 : -1);  // SPEC: glove nudges ±1 px at 4 Hz break;
    case TORQUE_ON:   backlight(40, 200, 70); printLine(0, "TORQUE ON    6/6"); drawBar(100, 0); break;
    case READY:       backlight(40, 200, 70); printLine(0, "READY TO HOLD"); drawBar(66, 0); break;
    case SETTLING:
      if (arg <= 3) backlight(255, 40, 30); else backlight(255, 170, 0);
      printLine(0, "SETTLING     " + String(arg) + "/6");
      drawBar(34 + (arg * 32 + 3) / 6, 0);
      break;
  }
  shown = state; shownArg = arg;
}

void handleLine(String line) {
  line.trim();
  if (!line.startsWith("STATE ")) return;
  String rest = line.substring(6);
  int space = rest.indexOf(' ');
  String name = space < 0 ? rest : rest.substring(0, space);
  int value = space < 0 ? 0 : rest.substring(space + 1).toInt();
  State next;
  if (name == "SETTLING") next = SETTLING;
  else if (name == "READY") next = READY;
  else if (name == "ALIGNING") next = ALIGNING;
  else if (name == "TORQUE_ON") next = TORQUE_ON;
  else if (name == "NOT_ENABLED") next = NOT_ENABLED;
  else if (name == "STOPPED") next = STOPPED;
  else if (name == "LOCKED") next = LOCKED;
  else if (name == "OFFLINE") next = OFFLINE;
  else if (name == "UNKNOWN") next = UNKNOWN;
  else return;
  state = next; arg = constrain(value, 0, 6); lastMsg = millis();
}

void setup() {
  Serial.begin(BAUD);
  Wire.begin();
#if LCD_RGB
  lcd.begin(16, 2);
#else
  lcd.init();
  lcd.backlight();
#endif
  for (int i = 0; i < 4; i++) lcd.createChar(i, (uint8_t*)FIXED[i]);
  render();
}

void loop() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n') { handleLine(inbox); inbox = ""; }
    else if (inbox.length() < 40) inbox += ch;
  }
  if (millis() - lastMsg > NODATA_MS) state = NODATA;
  bool animate = state == ALIGNING && millis() - lastFrame >= 250;
  if (animate) { frame++; lastFrame = millis(); }
  if (state != shown || arg != shownArg || animate) render();
}
