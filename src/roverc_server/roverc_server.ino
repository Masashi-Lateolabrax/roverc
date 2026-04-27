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
static constexpr uint32_t I2C_HZ = 100000;

static constexpr uint8_t ROVERC_ADDR = 0x38;
static constexpr uint8_t REG_MOTOR = 0x00;

// Per-wheel sign flips. Adjust during bring-up if a wheel spins the wrong way.
static constexpr int8_t SIGN_M[4] = {+1, +1, +1, +1};  // FL, FR, RL, RR

WiFiUDP udp;

struct Motion {
  float vx = 0.0f;
  float vy = 0.0f;
  float wz = 0.0f;
  double t = 0.0;
};

struct ServerConfig {
  int max_motor = MAX_MOTOR;
  int kick_dur_ms = 0;
  float trim_fwd[4] = {1.0f, 1.0f, 1.0f, 1.0f};
  float trim_bwd[4] = {1.0f, 1.0f, 1.0f, 1.0f};
  float kick_fwd[4] = {1.0f, 1.0f, 1.0f, 1.0f};
  float kick_bwd[4] = {1.0f, 1.0f, 1.0f, 1.0f};
};

struct MotorState {
  int8_t last_sign = 0;
  uint32_t kick_start_ms = 0;
  bool in_kick = false;
};

static Motion g_cmd;
static ServerConfig g_cfg;
static MotorState g_motor_state[4];
static uint32_t g_last_packet_ms = 0;
static uint32_t g_packets_received = 0;
static uint32_t g_configs_received = 0;
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

static float clampf(float v, float lo, float hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

static void parse_array4(JsonArrayConst arr, float out[4], float lo, float hi) {
  if (arr.isNull() || arr.size() < 4) return;
  for (int i = 0; i < 4; ++i) {
    float v = arr[i] | 1.0f;
    out[i] = clampf(v, lo, hi);
  }
}

static void compute_motors(float vx, float vy, float wz, uint32_t now, int8_t out[4]) {
  float m[4] = {
    vx + vy + wz,
    vx - vy - wz,
    vx - vy + wz,
    vx + vy - wz,
  };
  float peak = fmaxf(fmaxf(fabsf(m[0]), fabsf(m[1])), fmaxf(fabsf(m[2]), fabsf(m[3])));
  float scale = (peak > 1.0f) ? (1.0f / peak) : 1.0f;
  scale *= static_cast<float>(g_cfg.max_motor);

  for (int i = 0; i < 4; ++i) {
    int8_t sign = (m[i] > 0.0f) ? 1 : (m[i] < 0.0f ? -1 : 0);
    if (sign != 0 && sign != g_motor_state[i].last_sign) {
      g_motor_state[i].kick_start_ms = now;
      g_motor_state[i].in_kick = true;
    }
    if (sign == 0) {
      g_motor_state[i].in_kick = false;
    } else if (g_motor_state[i].in_kick &&
               (now - g_motor_state[i].kick_start_ms) >= static_cast<uint32_t>(g_cfg.kick_dur_ms)) {
      g_motor_state[i].in_kick = false;
    }
    g_motor_state[i].last_sign = sign;

    float tr;
    if (sign == 0) {
      tr = 1.0f;  // unused
    } else if (g_motor_state[i].in_kick) {
      tr = (sign > 0) ? g_cfg.kick_fwd[i] : g_cfg.kick_bwd[i];
    } else {
      tr = (sign > 0) ? g_cfg.trim_fwd[i] : g_cfg.trim_bwd[i];
    }
    out[i] = clamp_int8(static_cast<int>(m[i] * scale * tr)) * SIGN_M[i];
  }
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

static void apply_config(JsonObjectConst cfg) {
  if (cfg["mx"].is<int>()) {
    int mx = cfg["mx"];
    if (mx < 0) mx = 0;
    if (mx > 127) mx = 127;
    g_cfg.max_motor = mx;
  }
  if (cfg["kdur"].is<int>()) {
    int kd = cfg["kdur"];
    if (kd < 0) kd = 0;
    if (kd > 2000) kd = 2000;
    g_cfg.kick_dur_ms = kd;
  }
  parse_array4(cfg["tf"], g_cfg.trim_fwd, 0.0f, 4.0f);
  parse_array4(cfg["tb"], g_cfg.trim_bwd, 0.0f, 4.0f);
  parse_array4(cfg["kf"], g_cfg.kick_fwd, 0.0f, 4.0f);
  parse_array4(cfg["kb"], g_cfg.kick_bwd, 0.0f, 4.0f);
  g_configs_received++;
}

static void poll_udp() {
  int len = udp.parsePacket();
  if (len <= 0) return;

  static char buf[512];
  int n = udp.read(buf, sizeof(buf) - 1);
  if (n <= 0) return;
  buf[n] = '\0';

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, buf, n);
  if (err) return;

  JsonObjectConst cfg = doc["cfg"];
  if (!cfg.isNull()) {
    apply_config(cfg);
    return;  // config-only packet; do not update motion
  }

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

  M5.Display.fillRect(0, 50, M5.Display.width(), M5.Display.height() - 50, BLACK);
  M5.Display.setCursor(0, 50);
  M5.Display.setTextColor(failsafe ? RED : GREEN, BLACK);
  M5.Display.printf("age %5lu ms\n", static_cast<unsigned long>(age));
  M5.Display.setTextColor(WHITE, BLACK);
  M5.Display.printf("rx %lu cfg %lu\n",
                    static_cast<unsigned long>(g_packets_received),
                    static_cast<unsigned long>(g_configs_received));
  M5.Display.printf("m %4d %4d\n", g_motors[0], g_motors[1]);
  M5.Display.printf("  %4d %4d\n", g_motors[2], g_motors[3]);

  int bat_pct = M5.Power.getBatteryLevel();
  int bat_mv = M5.Power.getBatteryVoltage();
  bool charging = M5.Power.isCharging();
  uint16_t bat_color = (bat_pct >= 0 && bat_pct < 20) ? RED : WHITE;
  M5.Display.setTextColor(bat_color, BLACK);
  M5.Display.printf("bat %3d%% %4dmV%s\n",
                    bat_pct, bat_mv, charging ? " +" : "");
  M5.Display.setTextColor(WHITE, BLACK);
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(3);
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
    g_cmd = Motion{};
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
    compute_motors(vx, vy, wz, now, g_motors);
    send_motors_i2c(g_motors);
  }

  update_lcd();
}
