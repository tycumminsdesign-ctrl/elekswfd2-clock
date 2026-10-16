// WFD2 panel mapping firmware — serial-driven WS2812 chain walker.
//
// Purpose: discover the LED data GPIO, the total LED count, and the mapping of
// chain index -> physical element on the EleksWFD 2 (ESP32-S3) custom LED panel.
// Talk to it over the native USB serial port at 115200 baud.
//
// Commands (one per line):
//   help                 show this help
//   pins                 list candidate GPIOs used by scanpins
//   pin <n>              set the LED data GPIO and re-init the strip
//   num <n>              set the LED count and re-init (default 512)
//   order <grb|rgb|brg|rbg|gbr|bgr>   set color order and re-init
//   bright <0-255>       set global brightness (default 40)
//   off                  all LEDs off
//   all [r g b]          all LEDs on (default dim white 40,40,40)
//   set <i> [r g b]      light ONLY led i (default white); others off
//   range <a> <b> [r g b]  light leds a..b inclusive; others off
//   walk [ms]            light each led in turn, printing its index (default 400ms)
//   count                binary-search helper: lights led at index N on 'set'
//   scanpins [ms]        drive "all white" on each candidate GPIO in turn,
//                        printing the pin, so you can see which one lights the panel
//
// Nothing here is destructive; the stock firmware can be restored any time by
// flashing the full 8MB backup.

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include <Wire.h>

static int      g_pin    = 8;      // starting guess; override with `pin <n>`
static uint16_t g_num    = 512;    // generous upper bound on chain length
static uint8_t  g_bright = 40;
static uint16_t g_order  = NEO_GRB + NEO_KHZ800;
static bool     g_walking = false;
static uint16_t g_walkIdx = 0;
static uint32_t g_walkMs  = 400;
static uint32_t g_lastStep = 0;

Adafruit_NeoPixel strip(g_num, g_pin, NEO_GRB + NEO_KHZ800);

// Broad set of generally-safe output GPIOs on the ESP32-S3.
// (Avoids USB D+/D- (19/20) and the octal-flash/PSRAM range 26-32.)
static const int CAND[] = {
  1,2,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,21,
  33,34,35,36,37,38,39,40,41,42,45,47,48
};
static const int NCAND = sizeof(CAND) / sizeof(CAND[0]);

static void reinit() {
  strip.updateType(g_order);
  strip.updateLength(g_num);
  strip.setPin(g_pin);
  strip.begin();
  strip.setBrightness(g_bright);
  strip.clear();
  strip.show();
}

static void help() {
  Serial.println(F("WFD2 mapper ready. Commands:"));
  Serial.println(F("  pin <n> | num <n> | order <grb..> | bright <n>"));
  Serial.println(F("  off | all [r g b] | set <i> [r g b] | range <a> <b> [r g b]"));
  Serial.println(F("  walk [ms] | scanpins [ms] | pins | help"));
  Serial.printf ("  state: pin=%d num=%u bright=%u\n", g_pin, g_num, g_bright);
}

static uint16_t parseOrder(const String& s) {
  String o = s; o.toLowerCase();
  if (o == "rgb") return NEO_RGB + NEO_KHZ800;
  if (o == "rbg") return NEO_RBG + NEO_KHZ800;
  if (o == "grb") return NEO_GRB + NEO_KHZ800;
  if (o == "gbr") return NEO_GBR + NEO_KHZ800;
  if (o == "brg") return NEO_BRG + NEO_KHZ800;
  if (o == "bgr") return NEO_BGR + NEO_KHZ800;
  return g_order;
}

static void allOn(uint8_t r, uint8_t g, uint8_t b) {
  for (uint16_t i = 0; i < g_num; i++) strip.setPixelColor(i, r, g, b);
  strip.show();
}

static void onlyOne(uint16_t idx, uint8_t r, uint8_t g, uint8_t b) {
  strip.clear();
  if (idx < g_num) strip.setPixelColor(idx, r, g, b);
  strip.show();
}

static void onlyRange(uint16_t a, uint16_t b, uint8_t r, uint8_t g, uint8_t bl) {
  strip.clear();
  for (uint16_t i = a; i <= b && i < g_num; i++) strip.setPixelColor(i, r, g, bl);
  strip.show();
}

static void scanPins(uint32_t ms) {
  Serial.println(F("scanpins: watch the panel; note the pin printed when it lights."));
  for (int i = 0; i < NCAND; i++) {
    int p = CAND[i];
    strip.clear(); strip.show();
    strip.setPin(p);
    strip.begin();
    strip.setBrightness(g_bright);
    for (uint16_t k = 0; k < g_num; k++) strip.setPixelColor(k, 40, 40, 40);
    strip.show();
    Serial.printf(">> testing GPIO %d  (%d/%d)\n", p, i + 1, NCAND);
    delay(ms);
  }
  strip.clear(); strip.setPin(g_pin); strip.begin(); strip.show();
  Serial.println(F("scanpins done. Use `pin <n>` to lock in the one that lit up."));
}

// Scan for I2C devices across every ordered pair of candidate pins.
// Prints (sda, scl, addr) for any chip that ACKs. Address hints at the chip:
//   0x70-0x77 HT16K33 ; 0x50/0x5x IS31FL373x ; 0x58-0x5B AW9523 ; 0x3C/0x3D SSD13xx
static void i2cScan() {
  static const int P[] = {
    1,2,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,21,
    33,34,35,36,37,38,39,40,41,42,43,44,45,47,48
  };
  static const int NP = sizeof(P)/sizeof(P[0]);
  // Addresses used by common LED-matrix/segment driver chips.
  static const uint8_t A[] = {
    0x3C,0x3D,                                   // SSD13xx OLED (sanity)
    0x40,0x41,0x42,0x43,                          // PCA / misc
    0x50,0x51,0x52,0x53,0x54,0x55,0x56,0x57,      // IS31FL373x
    0x58,0x59,0x5A,0x5B,                           // AW9523 / IS31
    0x60,0x61,0x62,0x63,0x64,0x65,0x66,0x67,      // IS31FL37xx alt / SAA
    0x68,0x69,0x6A,0x6B,                           // RTC-ish / drivers
    0x70,0x71,0x72,0x73,0x74,0x75,0x76,0x77        // HT16K33
  };
  static const int NA = sizeof(A)/sizeof(A[0]);
  Serial.println(F("i2cscan: fast sweep (curated addrs, short timeout)..."));
  int hits = 0;
  for (int si = 0; si < NP; si++) {
    for (int ci = 0; ci < NP; ci++) {
      if (si == ci) continue;
      int sda = P[si], scl = P[ci];
      Wire.end();
      if (!Wire.begin(sda, scl, 100000)) continue;
      Wire.setTimeOut(6);
      for (int k = 0; k < NA; k++) {
        Wire.beginTransmission(A[k]);
        if (Wire.endTransmission() == 0) {
          Serial.printf("HIT sda=%d scl=%d addr=0x%02X\n", sda, scl, A[k]);
          hits++;
        }
      }
    }
    Serial.printf("  ..SDA=%d done\n", P[si]);
    yield();
  }
  Wire.end();
  Serial.printf("i2cscan done. %d hit(s).\n", hits);
}

// Probe ONE pin pair across the full address range.
// A real bus shows a small number of hits; a stuck-low SDA ACKs ~everything.
static void probePair(int sda, int scl) {
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(6);
  int n = 0; String list;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) { n++; if (n <= 12) { list += " 0x"; list += String(addr, HEX); } }
  }
  Serial.printf("probe sda=%d scl=%d -> %d hit(s):%s%s\n", sda, scl, n,
                list.c_str(), n > 12 ? " ...(likely stuck line)" : "");
  Wire.end();
}

// Try to drive an HT16K33 (very common LED clock/matrix driver).
// Turns on the oscillator, display on, max brightness, then fills all display
// RAM so EVERY connected LED should light. Watch the panel.
static void ht16k33Test(int sda, int scl, uint8_t addr, bool fill) {
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(10);
  auto cmd = [&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); return Wire.endTransmission(); };
  int e1 = cmd(0x21);              // oscillator on
  int e2 = cmd(0x81);              // display on, no blink
  int e3 = cmd(0xEF);              // brightness max
  Wire.beginTransmission(addr);    // write all 16 RAM bytes
  Wire.write(0x00);
  for (int i = 0; i < 16; i++) Wire.write(fill ? 0xFF : 0x00);
  int e4 = Wire.endTransmission();
  Serial.printf("ht16k33 addr=0x%02X sda=%d scl=%d fill=%d errs=%d,%d,%d,%d\n",
                addr, sda, scl, fill ? 1 : 0, e1, e2, e3, e4);
  Wire.end();
}

// Light the driver's RAM one byte at a time so we can map bits -> panel zones.
// ms = dwell per byte. Prints the byte index as it goes.
static void byteWalk(int sda, int scl, uint8_t addr, uint32_t ms) {
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(10);
  auto cmd = [&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  Serial.println(F("bytewalk: watch the panel and note what lights at each step"));
  for (int b = 0; b < 16; b++) {
    Wire.beginTransmission(addr);
    Wire.write(0x00);
    for (int i = 0; i < 16; i++) Wire.write(i == b ? 0xFF : 0x00);
    Wire.endTransmission();
    Serial.printf("  byte %d\n", b);
    delay(ms);
  }
  // leave everything off
  Wire.beginTransmission(addr);
  Wire.write(0x00);
  for (int i = 0; i < 16; i++) Wire.write(0x00);
  Wire.endTransmission();
  Serial.println(F("bytewalk done"));
  Wire.end();
}

// Standard 7-segment bit patterns, bit0=A .. bit6=G, bit7=DP.
static const uint8_t SEG7[10] = {
  0x3F,0x06,0x5B,0x4F,0x66,0x6D,0x7D,0x07,0x7F,0x6F
};

// Write digits 0-9 into consecutive driver RAM bytes starting at byte 0.
// If the panel's segment wiring is standard, "12345" should read as 12345.
static void showDigits(int sda, int scl, uint8_t addr, const String& s) {
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(10);
  auto cmd = [&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  uint8_t ram[16]; for (int i = 0; i < 16; i++) ram[i] = 0;
  for (int i = 0; i < 16 && i < (int)s.length(); i++) {
    char c = s[i];
    if (c >= '0' && c <= '9') ram[i] = SEG7[c - '0'];
    else if (c == '-')        ram[i] = 0x40;
    else                       ram[i] = 0x00;
  }
  Wire.beginTransmission(addr);
  Wire.write(0x00);
  for (int i = 0; i < 16; i++) Wire.write(ram[i]);
  int e = Wire.endTransmission();
  Serial.printf("showDigits \"%s\" err=%d\n", s.c_str(), e);
  Wire.end();
}

// Write one explicit byte value to one RAM byte (for segment bit hunting).
static void setByte(int sda, int scl, uint8_t addr, int idx, uint8_t val) {
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(10);
  auto cmd = [&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  Wire.beginTransmission(addr);
  Wire.write(0x00);
  for (int i = 0; i < 16; i++) Wire.write(i == idx ? val : 0x00);
  int e = Wire.endTransmission();
  Serial.printf("setByte idx=%d val=0x%02X err=%d\n", idx, val, e);
  Wire.end();
}

// Resumable pin-pair scan. Pair index = si*NP + ci, so a host script can
// restart us after a bus hang and continue from the next index.
static const int SP[] = {
  1,2,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,21,
  33,34,35,36,37,38,39,40,41,42,43,44,45,47,48
};
static const int NSP = sizeof(SP)/sizeof(SP[0]);

static void scanPairs(int startIdx, int count) {
  static const uint8_t A[] = {
    0x3C,0x3D,0x40,0x41,0x42,0x43,
    0x50,0x51,0x52,0x53,0x54,0x55,0x56,0x57,
    0x58,0x59,0x5A,0x5B,0x5C,0x5D,0x5E,0x5F,
    0x60,0x61,0x62,0x63,0x64,0x65,0x66,0x67,
    0x68,0x69,0x6A,0x6B,0x6C,0x6D,0x6E,0x6F,
    0x70,0x71,0x72,0x73,0x74,0x75,0x76,0x77
  };
  static const int NA = sizeof(A)/sizeof(A[0]);
  int total = NSP * NSP;
  int end = startIdx + count; if (end > total) end = total;
  for (int idx = startIdx; idx < end; idx++) {
    int si = idx / NSP, ci = idx % NSP;
    if (si == ci) continue;
    int sda = SP[si], scl = SP[ci];
    if (sda == 8 && scl == 9) continue;          // already-known bus
    Serial.printf("P %d %d %d\n", idx, sda, scl); // checkpoint BEFORE touching bus
    Serial.flush();
    Wire.end();
    if (!Wire.begin(sda, scl, 100000)) continue;
    Wire.setTimeOut(4);
    int hits = 0;
    for (int k = 0; k < NA; k++) {
      Wire.beginTransmission(A[k]);
      if (Wire.endTransmission() == 0) { hits++; if (hits <= 4) Serial.printf("HIT %d %d 0x%02X\n", sda, scl, A[k]); }
    }
    if (hits > 8) Serial.printf("  (stuck? %d acks)\n", hits);
    Wire.end();
    yield();
  }
  Serial.printf("SCANEND %d\n", end);
}

// Careful scan of ONE bus: long timeout, several retries per address.
// Catches chips a fast scan can miss.
static void probeCareful(int sda, int scl) {
  Wire.end();
  if (!Wire.begin(sda, scl, 50000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(50);
  int n = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    for (int try_ = 0; try_ < 3; try_++) {
      Wire.beginTransmission(addr);
      if (Wire.endTransmission() == 0) { Serial.printf("HIT2 0x%02X (try %d)\n", addr, try_); n++; break; }
      delay(2);
    }
    yield();
  }
  Serial.printf("probe2 sda=%d scl=%d hits=%d\n", sda, scl, n);
  Wire.end();
}

// Write `count` bytes of 0xFF into driver RAM (HT16K33 has 16; a bigger chip
// may light more of the panel if we push further).
static void fillN(int sda, int scl, uint8_t addr, int count) {
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(20);
  auto cmd = [&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  Wire.beginTransmission(addr);
  Wire.write(0x00);
  for (int i = 0; i < count; i++) Wire.write(0xFF);
  int e = Wire.endTransmission();
  Serial.printf("fillN count=%d err=%d\n", count, e);
  Wire.end();
}

// Read driver RAM back. Reveals how many bytes the chip really has:
// an HT16K33 mirrors 16 bytes; a larger chip will hold more distinct data.
static void dumpRam(int sda, int scl, uint8_t addr, int n) {
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(30);
  Wire.beginTransmission(addr); Wire.write(0x00); Wire.endTransmission(false);
  int got = Wire.requestFrom((int)addr, n);
  Serial.printf("dump requested=%d got=%d:", n, got);
  for (int i = 0; i < got && Wire.available(); i++) Serial.printf(" %02X", Wire.read());
  Serial.println();
  Wire.end();
}

// Targeted hunt for more HT16K33-family chips (0x70-0x77) across pin pairs.
// Pre-checks each pin pair with internal pullups: a real (or free) bus reads
// HIGH on both lines; a stuck-LOW line is skipped, which avoids the bus hangs
// that killed earlier scans.
static const int HP[] = {
  0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,21,
  33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48
};
static const int NHP = sizeof(HP)/sizeof(HP[0]);

static void huntHT(int startIdx, int count) {
  int total = NHP * NHP;
  int end = startIdx + count; if (end > total) end = total;
  for (int idx = startIdx; idx < end; idx++) {
    int si = idx / NHP, ci = idx % NHP;
    if (si == ci) continue;
    int sda = HP[si], scl = HP[ci];
    Serial.printf("H %d %d %d\n", idx, sda, scl); Serial.flush();
    // pullup pre-check: skip pairs where a line is held low
    pinMode(sda, INPUT_PULLUP); pinMode(scl, INPUT_PULLUP);
    delay(2);
    if (digitalRead(sda) == LOW || digitalRead(scl) == LOW) continue;
    Wire.end();
    if (!Wire.begin(sda, scl, 100000)) continue;
    Wire.setTimeOut(8);
    for (uint8_t addr = 0x70; addr <= 0x77; addr++) {
      Wire.beginTransmission(addr);
      if (Wire.endTransmission() == 0) Serial.printf("FOUND %d %d 0x%02X\n", sda, scl, addr);
    }
    Wire.end();
    yield();
  }
  Serial.printf("HUNTEND %d\n", end);
}

// Write `val` into `count` RAM bytes starting at `start`, zeroing bytes 0..start-1.
// Lets us test whether the controller has more than an HT16K33's 16 bytes.
static void writeRange(int sda,int scl,uint8_t addr,int start,int count,uint8_t val,int totalClear){
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(20);
  auto cmd=[&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  Wire.beginTransmission(addr);
  Wire.write(0x00);
  for (int i=0;i<totalClear;i++) Wire.write((i>=start && i<start+count) ? val : 0x00);
  int e=Wire.endTransmission();
  Serial.printf("writeRange start=%d count=%d total=%d err=%d\n", start, count, totalClear, e);
  Wire.end();
}

// Light one chunk of driver RAM at a time across a large space, so we can
// find the total RAM size and map chunk -> panel zone in a single pass.
static void chunkWalk(int sda,int scl,uint8_t addr,int chunk,int total,uint32_t ms){
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(30);
  auto cmd=[&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  int nchunks = total / chunk;
  Serial.printf("chunkwalk: %d chunks of %d bytes\n", nchunks, chunk);
  for (int c = 0; c < nchunks; c++) {
    int lo = c*chunk, hi = lo+chunk;
    Wire.beginTransmission(addr);
    Wire.write(0x00);
    for (int i=0;i<total;i++) Wire.write((i>=lo && i<hi) ? 0xFF : 0x00);
    Wire.endTransmission();
    Serial.printf("  chunk %d = bytes %d-%d\n", c, lo, hi-1);
    delay(ms);
  }
  Wire.beginTransmission(addr); Wire.write(0x00);
  for (int i=0;i<total;i++) Wire.write(0x00);
  Wire.endTransmission();
  Serial.println("chunkwalk done");
  Wire.end();
}

// Film-friendly RAM walk. Brackets the sequence with full-brightness ALL-ON
// markers so the exact start/end can be located in a video, then lights one
// chunk of driver RAM at a time with fixed dwell.
static void filmWalk(int sda,int scl,uint8_t addr,int chunk,int total,uint32_t ms){
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(30);
  auto cmd=[&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  auto blast=[&](bool on){
    Wire.beginTransmission(addr); Wire.write(0x00);
    for (int i=0;i<total;i++) Wire.write(on?0xFF:0x00);
    Wire.endTransmission();
  };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  int nchunks = total / chunk;
  Serial.printf("FILMWALK chunks=%d chunk=%d total=%d ms=%u\n", nchunks, chunk, total, ms);
  blast(true);  Serial.println("MARKER_START"); delay(3000);
  blast(false); delay(2000);
  for (int c = 0; c < nchunks; c++) {
    int lo=c*chunk, hi=lo+chunk;
    Wire.beginTransmission(addr); Wire.write(0x00);
    for (int i=0;i<total;i++) Wire.write((i>=lo&&i<hi)?0xFF:0x00);
    Wire.endTransmission();
    Serial.printf("STEP %d bytes %d-%d\n", c, lo, hi-1);
    delay(ms);
  }
  blast(false); delay(1000);
  blast(true);  Serial.println("MARKER_END"); delay(3000);
  blast(false);
  Serial.println("FILMWALK done");
  Wire.end();
}

// Film-friendly walk over a byte RANGE, one byte at a time, inside a fixed
// `total`-byte frame. Same ALL-ON markers for video sync.
static void filmRange(int sda,int scl,uint8_t addr,int start,int end,int total,uint32_t ms){
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(30);
  auto cmd=[&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  auto blast=[&](bool on){
    Wire.beginTransmission(addr); Wire.write(0x00);
    for (int i=0;i<total;i++) Wire.write(on?0xFF:0x00);
    Wire.endTransmission();
  };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  Serial.printf("FILMRANGE %d..%d total=%d ms=%u steps=%d\n", start, end, total, ms, end-start+1);
  blast(true);  Serial.println("MARKER_START"); delay(3000);
  blast(false); delay(2000);
  for (int b=start; b<=end; b++) {
    Wire.beginTransmission(addr); Wire.write(0x00);
    for (int i=0;i<total;i++) Wire.write(i==b ? 0xFF : 0x00);
    Wire.endTransmission();
    Serial.printf("STEP byte %d\n", b);
    delay(ms);
  }
  blast(false); delay(1000);
  blast(true);  Serial.println("MARKER_END"); delay(3000);
  blast(false);
  Serial.println("FILMRANGE done");
  Wire.end();
}

// Binary-coded ("bit plane") mapping: instead of one photo per byte, light the
// subset of bytes whose index has bit `plane` set. Photographing planes 0..3
// lets a 16-byte range be decoded from just 4 images -- each element's byte
// index is the binary number formed by which planes it appears in.
// plane < 0 means "light the whole range" (the reference shot).
static void bitPlane(int sda,int scl,uint8_t addr,int start,int count,int plane,int total){
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(30);
  auto cmd=[&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  Wire.beginTransmission(addr); Wire.write(0x00);
  for (int i=0;i<total;i++) {
    bool on=false;
    if (i>=start && i<start+count) {
      int k=i-start;
      on = (plane < 0) ? true : ((k >> plane) & 1);
    }
    Wire.write(on?0xFF:0x00);
  }
  int e=Wire.endTransmission();
  if (plane<0) Serial.printf("PLANE ref (all %d..%d) err=%d\n", start, start+count-1, e);
  else         Serial.printf("PLANE %d (bytes with bit%d set) err=%d\n", plane, plane, e);
  Wire.end();
}

// Write an arbitrary VALUE into a byte range (rest of the frame zeroed).
// Setting the same single bit in every byte lights one "row" of the LED
// matrix, which reveals whether bit = which meter and byte = position along it.
static void valueRange(int sda,int scl,uint8_t addr,int start,int count,uint8_t val,int total){
  Wire.end();
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("begin failed"); return; }
  Wire.setTimeOut(30);
  auto cmd=[&](uint8_t c){ Wire.beginTransmission(addr); Wire.write(c); Wire.endTransmission(); };
  cmd(0x21); cmd(0x81); cmd(0xEF);
  Wire.beginTransmission(addr); Wire.write(0x00);
  for (int i=0;i<total;i++) Wire.write((i>=start && i<start+count) ? val : 0x00);
  int e=Wire.endTransmission();
  Serial.printf("valueRange %d..%d val=0x%02X err=%d\n", start, start+count-1, val, e);
  Wire.end();
}

static void handle(String line) {
  line.trim();
  if (!line.length()) return;
  int sp = line.indexOf(' ');
  String cmd = (sp < 0) ? line : line.substring(0, sp);
  String rest = (sp < 0) ? ""   : line.substring(sp + 1);
  cmd.toLowerCase();

  // tokenize rest into up to 4 ints
  long a[8] = {0,0,0,0,0,0,0,0}; int na = 0;
  { String r = rest; r.trim();
    while (r.length() && na < 8) {
      int s = r.indexOf(' ');
      String tok = (s < 0) ? r : r.substring(0, s);
      a[na++] = tok.toInt();
      r = (s < 0) ? "" : r.substring(s + 1); r.trim();
    } }

  if (cmd == "help") { help(); }
  else if (cmd == "pins") {
    Serial.print(F("candidates:"));
    for (int i = 0; i < NCAND; i++) Serial.printf(" %d", CAND[i]);
    Serial.println();
  }
  else if (cmd == "pin")   { g_pin = a[0]; reinit(); Serial.printf("pin=%d\n", g_pin); }
  else if (cmd == "num")   { g_num = a[0]; reinit(); Serial.printf("num=%u\n", g_num); }
  else if (cmd == "order") { g_order = parseOrder(rest); reinit(); Serial.printf("order set\n"); }
  else if (cmd == "bright"){ g_bright = a[0]; strip.setBrightness(g_bright); strip.show(); Serial.printf("bright=%u\n", g_bright); }
  else if (cmd == "off")   { g_walking = false; strip.clear(); strip.show(); Serial.println("off"); }
  else if (cmd == "all")   { uint8_t r=na>=3?a[0]:40,g=na>=3?a[1]:40,b=na>=3?a[2]:40; allOn(r,g,b); Serial.println("all on"); }
  else if (cmd == "set")   { uint16_t i=a[0]; uint8_t r=na>=4?a[1]:255,g=na>=4?a[2]:255,b=na>=4?a[3]:255; onlyOne(i,r,g,b); Serial.printf("set %u\n", i); }
  else if (cmd == "range") { uint16_t x=a[0],y=a[1]; uint8_t r=na>=5?a[2]:255,g=na>=5?a[3]:255,b=na>=5?a[4]:255; onlyRange(x,y,r,g,b); Serial.printf("range %u..%u\n", x, y); }
  else if (cmd == "walk")  { g_walkMs = na>=1 ? (uint32_t)a[0] : 400; g_walking = true; g_walkIdx = 0; g_lastStep = 0; Serial.printf("walk @%ums\n", g_walkMs); }
  else if (cmd == "scanpins") { scanPins(na>=1 ? (uint32_t)a[0] : 1500); }
  else if (cmd == "i2cscan")  { i2cScan(); }
  else if (cmd == "probe")    { probePair(a[0], a[1]); }
  else if (cmd == "ht")       { ht16k33Test(a[0], a[1], (uint8_t)a[2], na < 4 || a[3] != 0); }
  else if (cmd == "bytewalk") { byteWalk(a[0], a[1], (uint8_t)a[2], na >= 4 ? (uint32_t)a[3] : 2500); }
  else if (cmd == "show")     { // show <sda> <scl> <addr> <digits>
    int p1=rest.indexOf(' '), p2=rest.indexOf(' ',p1+1), p3=rest.indexOf(' ',p2+1);
    if (p3 > 0) showDigits(rest.substring(0,p1).toInt(), rest.substring(p1+1,p2).toInt(),
                           (uint8_t)rest.substring(p2+1,p3).toInt(), rest.substring(p3+1));
    else Serial.println("usage: show <sda> <scl> <addr> <digits>"); }
  else if (cmd == "byte")     { setByte(a[0], a[1], (uint8_t)a[2], a[3], (uint8_t)(na>=5?a[4]:0xFF)); }
  else if (cmd == "scanpairs"){ scanPairs(a[0], na>=2 ? a[1] : 40); }
  else if (cmd == "probe2")   { probeCareful(a[0], a[1]); }
  else if (cmd == "hunt")     { huntHT(a[0], na>=2 ? a[1] : 80); }
  else if (cmd == "val")      { valueRange(a[0],a[1],(uint8_t)a[2],a[3],a[4],(uint8_t)a[5],na>=7?a[6]:128); }
  else if (cmd == "plane")    { bitPlane(a[0],a[1],(uint8_t)a[2],a[3],a[4],a[5],na>=7?a[6]:128); }
  else if (cmd == "filmrange"){ filmRange(a[0],a[1],(uint8_t)a[2],a[3],a[4],na>=6?a[5]:128,na>=7?(uint32_t)a[6]:3000); }
  else if (cmd == "filmwalk") { filmWalk(a[0],a[1],(uint8_t)a[2],na>=4?a[3]:16,na>=5?a[4]:128,na>=6?(uint32_t)a[5]:4000); }
  else if (cmd == "chunkwalk"){ chunkWalk(a[0],a[1],(uint8_t)a[2],na>=4?a[3]:16,na>=5?a[4]:128,na>=6?(uint32_t)a[5]:5000); }
  else if (cmd == "wr")       { writeRange(a[0],a[1],(uint8_t)a[2],a[3],na>=5?a[4]:16,0xFF,na>=6?a[5]:64); }
  else if (cmd == "dump")     { dumpRam(a[0], a[1], (uint8_t)a[2], na>=4 ? a[3] : 16); }
  else if (cmd == "filln")    { fillN(a[0], a[1], (uint8_t)a[2], na>=4 ? a[3] : 16); }
  else { Serial.printf("? %s\n", cmd.c_str()); }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  reinit();
  Serial.println();
  help();
}

void loop() {
  static String buf;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { if (buf.length()) { handle(buf); buf = ""; } }
    else buf += c;
  }
  if (g_walking) {
    uint32_t now = millis();
    if (now - g_lastStep >= g_walkMs) {
      g_lastStep = now;
      onlyOne(g_walkIdx, 255, 255, 255);
      Serial.printf("walk idx=%u\n", g_walkIdx);
      g_walkIdx++;
      if (g_walkIdx >= g_num) { g_walking = false; Serial.println("walk done"); }
    }
  }
}
