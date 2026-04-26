// RoverC teleop server: WiFi STA + UDP receiver + I2C bridge to RoverC HAT.
// Target board: M5StickC Plus2 (FQBN esp32:esp32:m5stack_stickc_plus2).

#include <M5Unified.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <ArduinoJson.h>

#include "secrets.h"

// I2C wiring on the RoverC HAT bus (StickC Plus2 P1 STICKIO header).
// SDA = G0 (pin 5), SCL = G26 (pin 3).
static constexpr int PIN_SDA = 0;
static constexpr int PIN_SCL = 26;
static constexpr uint32_t I2C_HZ = 400000;

static constexpr uint8_t ROVERC_ADDR = 0x38;
static constexpr uint8_t REG_MOTOR = 0x00;

// Per-wheel sign flips. Adjust during bring-up if a wheel spins the wrong way.
static constexpr int8_t SIGN_M1 = +1;  // front-left
static constexpr int8_t SIGN_M2 = +1;  // front-right
static constexpr int8_t SIGN_M3 = +1;  // rear-left
static constexpr int8_t SIGN_M4 = +1;  // rear-right

WiFiUDP udp;

struct Command {
  float vx = 0.0f;
  float vy = 0.0f;
  float wz = 0.0f;
  double t = 0.0;
};

static Command g_cmd;
static uint32_t g_last_packet_ms = 0;
static uint32_t g_packets_received = 0;
static IPAddress g_last_sender;
static int8_t g_motors[4] = {0, 0, 0, 0};

static const uint32_t CONTROL_PERIOD_MS = 1000UL / CONTROL_RATE_HZ;
static uint32_t g_next_tick_ms = 0;
static uint32_t g_next_lcd_ms = 0;

static int8_t clamp_int8(int v) {
  if (v > 127) return 127;
  if (v < -127) return -127;
  return static_cast<int8_t>(v);
}

static void mecanum_to_motors(float vx, float vy, float wz, int8_t out[4]) {
  float m1 = vx + vy + wz;
  float m2 = vx - vy - wz;
  float m3 = vx - vy + wz;
  float m4 = vx + vy - wz;

  float peak = fmaxf(fmaxf(fabsf(m1), fabsf(m2)), fmaxf(fabsf(m3), fabsf(m4)));
  float scale = (peak > 1.0f) ? (1.0f / peak) : 1.0f;
  scale *= static_cast<float>(MAX_MOTOR);

  out[0] = clamp_int8(static_cast<int>(m1 * scale)) * SIGN_M1;
  out[1] = clamp_int8(static_cast<int>(m2 * scale)) * SIGN_M2;
  out[2] = clamp_int8(static_cast<int>(m3 * scale)) * SIGN_M3;
  out[3] = clamp_int8(static_cast<int>(m4 * scale)) * SIGN_M4;
}

static void send_motors_i2c(const int8_t m[4]) {
  Wire.beginTransmission(ROVERC_ADDR);
  Wire.write(REG_MOTOR);
  Wire.write(static_cast<uint8_t>(m[0]));
  Wire.write(static_cast<uint8_t>(m[1]));
  Wire.write(static_cast<uint8_t>(m[2]));
  Wire.write(static_cast<uint8_t>(m[3]));
  Wire.endTransmission();
}

static void connect_wifi() {
  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(0, 0);
  M5.Display.printf("WiFi:\n%s\n", WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);
  delay(100);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  uint32_t deadline = millis() + 15000;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
    M5.Display.print(".");
    delay(250);
  }

  if (WiFi.status() != WL_CONNECTED) {
    M5.Display.fillScreen(RED);
    M5.Display.setCursor(0, 0);
    M5.Display.println("WIFI FAIL");
    return;
  }

  udp.stop();
  udp.begin(SERVER_PORT);

  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(0, 0);
  M5.Display.printf("OK %s\n", WIFI_SSID);
  M5.Display.printf("IP %s\n", WiFi.localIP().toString().c_str());
  M5.Display.printf("PORT %u\n", SERVER_PORT);
}

static void poll_udp() {
  int len = udp.parsePacket();
  if (len <= 0) return;

  static char buf[256];
  int n = udp.read(buf, sizeof(buf) - 1);
  if (n <= 0) return;
  buf[n] = '\0';

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, buf, n);
  if (err) return;

  g_cmd.vx = doc["vx"] | 0.0f;
  g_cmd.vy = doc["vy"] | 0.0f;
  g_cmd.wz = doc["wz"] | 0.0f;
  g_cmd.t = doc["t"] | 0.0;
  g_last_packet_ms = millis();
  g_last_sender = udp.remoteIP();
  g_packets_received++;
}

static void update_lcd() {
  uint32_t now = millis();
  if (now < g_next_lcd_ms) return;
  g_next_lcd_ms = now + 200;

  uint32_t age = now - g_last_packet_ms;
  bool failsafe = (age > FAILSAFE_MS);

  M5.Display.fillRect(0, 60, M5.Display.width(), M5.Display.height() - 60, BLACK);
  M5.Display.setCursor(0, 60);
  M5.Display.setTextColor(failsafe ? RED : GREEN, BLACK);
  M5.Display.printf("age %5lu ms\n", static_cast<unsigned long>(age));
  M5.Display.setTextColor(WHITE, BLACK);
  M5.Display.printf("rx %lu\n", static_cast<unsigned long>(g_packets_received));
  M5.Display.printf("m %4d %4d\n", g_motors[0], g_motors[1]);
  M5.Display.printf("  %4d %4d\n", g_motors[2], g_motors[3]);
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(0);
  M5.Display.setTextSize(2);
  M5.Display.fillScreen(BLACK);

  Wire.begin(PIN_SDA, PIN_SCL, I2C_HZ);

  // Stop motors immediately at boot in case the HAT retained state.
  int8_t zero[4] = {0, 0, 0, 0};
  send_motors_i2c(zero);

  Serial.begin(115200);
  connect_wifi();

  g_last_packet_ms = millis() - FAILSAFE_MS - 1;  // start in failsafe
  g_next_tick_ms = millis();
}

void loop() {
  M5.update();

  if (M5.BtnA.wasPressed()) {
    connect_wifi();
  }
  if (M5.BtnB.wasPressed()) {
    g_cmd = Command{};
    g_last_packet_ms = millis() - FAILSAFE_MS - 1;
  }

  poll_udp();

  uint32_t now = millis();
  if (static_cast<int32_t>(now - g_next_tick_ms) >= 0) {
    g_next_tick_ms += CONTROL_PERIOD_MS;
    if (static_cast<int32_t>(now - g_next_tick_ms) > 200) {
      g_next_tick_ms = now + CONTROL_PERIOD_MS;
    }

    bool failsafe = (now - g_last_packet_ms > FAILSAFE_MS);
    float vx = failsafe ? 0.0f : g_cmd.vx;
    float vy = failsafe ? 0.0f : g_cmd.vy;
    float wz = failsafe ? 0.0f : g_cmd.wz;
    mecanum_to_motors(vx, vy, wz, g_motors);
    send_motors_i2c(g_motors);
  }

  update_lcd();
}
