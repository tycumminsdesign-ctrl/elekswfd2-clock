// EleksWFD 2 -- USB display bridge
//
// Replaces the stock EleksMaker firmware. Deliberately dumb: it owns no fonts,
// no clock, no weather, no layout. The Mac renders a complete 64-byte frame and
// this pushes it to the panel's LED controller. Everything interesting lives in
// Python where it can be changed without reflashing.
//
// Consequences worth stating plainly:
//   * No WiFi stack is started, so the device joins no network, contacts no
//     server, and cannot leak the Wi-Fi password the way the stock firmware did.
//   * The panel is dark unless a Mac is driving it. That is the intent -- the
//     display belongs to whichever machine holds the cable.
//
// Panel: one I2C LED controller at 0x73 on SDA=8/SCL=9, 64 bytes of RAM, one
// bit per LED (512 LEDs). Writes are a pointer byte (0x00) followed by the
// whole frame; the controller is write-only, reads always fail.
//
// Protocol (line based, 115200 8N1 over native USB CDC):
//   F<128 hex chars>\n   write a full 64-byte frame
//   B<0-15>\n            set brightness
//   X\n                  blank the panel
//   P\n                  ping -> replies "OK"
// Replies: "OK", or "ERR <reason>".

#include <Arduino.h>
#include <Wire.h>

namespace {

constexpr int      kSda        = 8;
constexpr int      kScl        = 9;
constexpr uint8_t  kAddr       = 0x73;
constexpr size_t   kFrameBytes = 64;

// HT16K33-style control registers, confirmed against this panel.
constexpr uint8_t kSysStandby = 0x20;   // system setup: oscillator OFF
constexpr uint8_t kOscOn      = 0x21;   // system setup: oscillator ON
constexpr uint8_t kRowOutput  = 0xA0;   // ROW/INT set: row-driver output, INT off
constexpr uint8_t kDisplayOn  = 0x81;   // display on, no blink
constexpr uint8_t kBrightBase = 0xE0;   // | 0..15

// If the host stops talking we blank rather than freeze: the controller holds
// its last frame forever, and a stale display is worse than a dark one.
constexpr uint32_t kHostTimeoutMs = 5000;

uint8_t  g_frame[kFrameBytes];
uint32_t g_lastFrameMs = 0;
bool     g_blanked     = true;

void command(uint8_t c) {
  Wire.beginTransmission(kAddr);
  Wire.write(c);
  Wire.endTransmission();
}

bool pushFrame(const uint8_t* data) {
  Wire.beginTransmission(kAddr);
  Wire.write(0x00);                       // RAM pointer
  Wire.write(data, kFrameBytes);
  return Wire.endTransmission() == 0;
}

void blank() {
  uint8_t zeros[kFrameBytes] = {0};
  pushFrame(zeros);
  g_blanked = true;
}

int hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// "F" + 128 hex chars. Parsed strictly: a malformed frame is rejected whole
// rather than drawn half-updated, which would show as a torn display.
void handleFrame(const String& line) {
  if (line.length() != 1 + kFrameBytes * 2) {
    Serial.printf("ERR frame length %u\n", line.length() - 1);
    return;
  }
  uint8_t buf[kFrameBytes];
  for (size_t i = 0; i < kFrameBytes; i++) {
    int hi = hexVal(line[1 + i * 2]);
    int lo = hexVal(line[2 + i * 2]);
    if (hi < 0 || lo < 0) {
      Serial.println("ERR frame hex");
      return;
    }
    buf[i] = (uint8_t)((hi << 4) | lo);
  }
  memcpy(g_frame, buf, kFrameBytes);
  if (pushFrame(g_frame)) {
    g_blanked = false;
    g_lastFrameMs = millis();
    Serial.println("OK");
  } else {
    Serial.println("ERR i2c");
  }
}

// "C" + hex byte pairs: send each byte to the controller as a standalone
// control command (its own I2C transaction). Used to hunt the cold-boot init
// sequence experimentally -- try a sequence, draw a test frame, read the panel
// off the camera, repeat -- without reflashing for every guess.
void handleCommands(const String& line) {
  int n = (line.length() - 1) / 2;
  for (int i = 0; i < n; i++) {
    int hi = hexVal(line[1 + i * 2]);
    int lo = hexVal(line[2 + i * 2]);
    if (hi < 0 || lo < 0) { Serial.println("ERR cmd hex"); return; }
    command((uint8_t)((hi << 4) | lo));
    delay(1);
  }
  Serial.printf("OK %d cmd(s)\n", n);
}

void handleLine(String line) {
  line.trim();
  if (line.isEmpty()) return;
  switch (line[0]) {
    case 'F': handleFrame(line); break;
    case 'C': handleCommands(line); break;
    case 'B': {
      int level = constrain(line.substring(1).toInt(), 0, 15);
      command(kBrightBase | (uint8_t)level);
      Serial.println("OK");
      break;
    }
    case 'X': blank(); Serial.println("OK"); break;
    case 'P': Serial.println("OK"); break;
    default:  Serial.println("ERR unknown"); break;
  }
}

}  // namespace

// Standard HT16K33 bring-up: oscillator on, display on (no blink), brightness.
// This is the same sequence Adafruit's driver uses and what ran this panel
// solidly for hours. (An experiment adding 0x20 standby + 0xA0 row-output made
// things worse, not better -- the real dark-panel cause is a marginal power
// path browning out the LED array, not the init.)
void initDisplay() {
  command(kOscOn);      delay(1);
  command(kDisplayOn);  delay(1);
  command(kBrightBase | 15);
  delay(1);
}

void setup() {
  Serial.begin(115200);
  Wire.begin(kSda, kScl, 400000);
  Wire.setTimeOut(20);
  initDisplay();
  blank();
}

void loop() {
  static String buf;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (buf.length()) { handleLine(buf); buf = ""; }
    } else if (buf.length() < 200) {
      buf += c;
    }
  }
  if (!g_blanked && millis() - g_lastFrameMs > kHostTimeoutMs) {
    blank();
  }
}
