// RoverC teleop server: WiFi STA + UDP receiver + I2C bridge to RoverC HAT.
// Target board: M5StickC Plus2 (FQBN esp32:esp32:m5stack_stickc_plus2).

#include <M5Unified.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <math.h>
#include <string.h>

#include "secrets.h"

// I2C wiring on the RoverC HAT bus (StickC Plus2 P1 STICKIO header).
// SDA = G0 (pin 5), SCL = G26 (pin 3). Used for the RoverC motor STM32 and
// the front stereo Timer Camera Xs (left/right).
static constexpr int PIN_SDA = 0;
static constexpr int PIN_SCL = 26;
// StickC Plus2 Grove port (Port A, side connector). Used for the fisheye
// Timer Camera F that mounts on the mast where the HAT bus does not reach.
// SDA = G32, SCL = G33.
static constexpr int PIN_GROVE_SDA = 32;
static constexpr int PIN_GROVE_SCL = 33;
static constexpr uint32_t I2C_HZ = 100000;

static constexpr uint8_t ROVERC_ADDR = 0x38;
static constexpr uint8_t REG_MOTOR = 0x00;

// Cameras serve an 8-byte status frame on master read:
//   [0..3] IPv4 octets  [4..5] http_port (LE)  [6] camera_ok  [7] wifi_ok
// left/right share the HAT bus with the RoverC. fisheye lives on the
// independent Grove bus.
static constexpr uint8_t CAM_ADDR_LEFT = 0x40;
static constexpr uint8_t CAM_ADDR_RIGHT = 0x41;
static constexpr uint8_t CAM_ADDR_FISHEYE = 0x42;
static constexpr size_t CAM_FRAME_SIZE = 8;
static constexpr uint32_t CAM_PROBE_INTERVAL_MS = 1000;

// MJPEG send-token broadcast. Round-robin a 1-byte write to the camera
// slaves at CAM_TOKEN_PERIOD_MS so each camera ends up with ~8 Hz tokens
// (3-way) offset evenly, preventing simultaneous frame transmits that hog
// 2.4 GHz airtime when multiple cameras emit a high-entropy JPEG at once.
static constexpr uint32_t CAM_TOKEN_PERIOD_MS = 42;
static constexpr uint8_t  CAM_TOKEN_BYTE = 0x01;
static uint32_t g_next_token_ms = 0;
static uint8_t  g_token_target = 0;  // 0=left, 1=right, 2=fisheye

// 25 Hz binary telemetry push to the last UDP sender (the PC client). 25 Hz
// is slow enough to coexist with two MJPEG streams on the same 2.4 GHz radio
// without obvious airtime contention. Disabled by default; PC opts in via
// `cfg.tel = true`.
static constexpr uint32_t TEL_PERIOD_MS = 40;
static constexpr uint8_t  TEL_MAGIC = 0xD1;
static uint32_t g_next_tel_ms = 0;

// Binary polynomial-coefficient packet format (sent as its own UDP datagram,
// not embedded in the JSON cfg envelope):
//   [0]      magic 0xC0
//   [1]      wheel  (0..3 = FL FR RL RR)
//   [2]      dir    (0=fwd, 1=bwd)
//   [3]      reserved (=0)
//   [4..7]   k_steady               (float LE) -- STEADY gain, p = k * s
//   [8..31]  kick c[0..POLY_MAX_ORDER]    (POLY_NCOEFS floats LE, monomial in t)
//   [32..55] brake c[0..POLY_MAX_ORDER]   (POLY_NCOEFS floats LE, monomial in t)
// Total 56 bytes. Idempotent on (wheel, dir); PC repeats each chunk to absorb
// LWIP rx-queue drops (rx queue ~6-8 packets), and 8 chunks (4 wheels × 2 dirs)
// form the full coefficient table.
static constexpr uint8_t  POLY_MAX_ORDER = 5;
static constexpr size_t   POLY_NCOEFS = POLY_MAX_ORDER + 1;
static constexpr uint8_t  POLY_CHUNK_MAGIC = 0xC0;
static constexpr size_t   POLY_CHUNK_BYTES = 4 + 4 + POLY_NCOEFS * 4 + POLY_NCOEFS * 4;
static_assert(POLY_CHUNK_BYTES == 56, "wire format size drift");

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
  int brake_dur_ms = 100;
  bool tel_en = false;
};

// Per-wheel phase machine. KICK breaks stiction at sign rising edge, STEADY
// applies user trim during sustained drive, BRAKE applies a counter-pulse
// (linear ramp envelope, encoded in the polynomial t-axis) when s drops to 0,
// IDLE is the rest state.
enum Phase : uint8_t { PH_IDLE = 0, PH_KICK = 1, PH_STEADY = 2, PH_BRAKE = 3 };

struct MotorState {
  Phase phase = PH_IDLE;
  uint32_t phase_start_ms = 0;
  int8_t last_sign = 0;
  float last_s_norm = 0.0f;  // m_i * norm from previous tick (snapshot source)
  float s_pre = 0.0f;        // normalized s at BRAKE entry (∈ [-1, 1])
  int8_t s_pre_sign = 0;     // sign of s_pre (selects brake fwd vs bwd cell)
};

// Univariate-in-t polynomial motor model. Per (wheel, dir):
//   p_kick(t)  = s · f_k(t),     f_k(0) = 0,        f_k(T_k) = k_steady
//   p_steady   = k_steady · s
//   p_brake(t) = s_pre · f_b(t), f_b(0) = k_steady, f_b(T_b) = 0
// `s` is the per-wheel mecanum-mixed normalized command, `s_pre` is the
// snapshot at STEADY → BRAKE entry, `t` is phase-relative time in seconds.
// Boundary conditions enforce continuity at phase transitions and are
// applied PC-side before the chunks land here; firmware just evaluates the
// monomial polynomial. `f_k`, `f_b` non-negativity (so direction is preserved)
// is also enforced PC-side via Bernstein control points + x² mapping.
struct Poly1D {
  float c[POLY_NCOEFS];
};

struct PerDirCoefs {
  float k_steady;
  Poly1D kick;
  Poly1D brake;
};

struct WheelCoefs {
  PerDirCoefs fwd;
  PerDirCoefs bwd;
};

struct CameraState {
  uint8_t addr;
  bool present = false;        // sticky: cleared only after MARK_NOT_PRESENT_AFTER probe failures in a row
  uint32_t last_seen_ms = 0;
  uint8_t ip[4] = {0, 0, 0, 0};
  uint16_t http_port = 0;
  bool camera_ok = false;
  bool wifi_ok = false;
  uint8_t fail_streak = 0;
};

// A single failed Wire.requestFrom can leave the I2C bus master state machine
// hung (SDA/SCL stuck low or driver internal flag stuck), and Arduino-ESP32
// has no automatic recovery -- so we must (1) absorb transient blips with a
// fail streak before clearing `present`, and (2) reset the master entirely
// when failures pile up. Without this, /control writes during Apply can wedge
// the camera probe path for ~30s+.
static constexpr uint8_t MARK_NOT_PRESENT_AFTER = 3;
static constexpr uint8_t RECOVER_BUS_AFTER = 5;
static uint8_t g_probe_fails_since_last_ok = 0;

static Motion g_cmd;
static ServerConfig g_cfg;
static MotorState g_motor_state[4];
static WheelCoefs g_poly[4];
static uint32_t g_last_packet_ms = 0;
static uint32_t g_packets_received = 0;
static uint32_t g_configs_received = 0;
static uint32_t g_poly_chunks_received = 0;
static IPAddress g_last_sender;
static uint16_t g_last_sender_port = 0;
static int8_t g_motors[4] = {0, 0, 0, 0};
// Last per-wheel normalized command (m_i * norm), telemetry "s" channel.
static float g_s_norm[4] = {0.0f, 0.0f, 0.0f, 0.0f};

static CameraState g_cam_left = {CAM_ADDR_LEFT};
static CameraState g_cam_right = {CAM_ADDR_RIGHT};
static CameraState g_cam_fisheye = {CAM_ADDR_FISHEYE};
static uint32_t g_next_cam_tick_ms = 0;

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

// Evaluate Σ c[k] * t^k over k ∈ {0..POLY_MAX_ORDER}. Slots beyond the
// calibrated polynomial degree are zero-padded by the PC, so this is just
// a fixed-size Horner-like loop.
static float eval_poly1d(const Poly1D &p, float t) {
  float result = 0.0f;
  float t_pow = 1.0f;
  for (size_t k = 0; k < POLY_NCOEFS; ++k) {
    result += p.c[k] * t_pow;
    t_pow *= t;
  }
  return result;
}

// Default at boot: k_steady = 1 (p = s during STEADY), kick polynomial linear
// 0 → 1 over T_k (matches the f_k(0)=0, f_k(T_k)=k boundary conditions),
// brake polynomial all-zero (no active brake until calibrate.py pushes
// constraint-respecting coefficients). T_k_sec = 0 collapses kick to zero.
static void init_poly_defaults() {
  float Tk_sec = static_cast<float>(g_cfg.kick_dur_ms) / 1000.0f;
  for (int i = 0; i < 4; ++i) {
    PerDirCoefs *dirs[2] = {&g_poly[i].fwd, &g_poly[i].bwd};
    for (int d = 0; d < 2; ++d) {
      dirs[d]->k_steady = 1.0f;
      memset(&dirs[d]->kick, 0, sizeof(Poly1D));
      memset(&dirs[d]->brake, 0, sizeof(Poly1D));
      if (Tk_sec > 0.0f) {
        dirs[d]->kick.c[1] = 1.0f / Tk_sec;  // f_k(t) = t / T_k
      }
    }
  }
}

// Apply a single binary 0xC0 polynomial chunk for one (wheel, dir). Rejects
// (and logs) any chunk whose floats include a non-finite value or a negative
// k_steady -- direction would flip on negative gain.
static void apply_poly_chunk(const uint8_t *buf, int n) {
  if (n < static_cast<int>(POLY_CHUNK_BYTES)) return;
  uint8_t wheel = buf[1];
  uint8_t dir   = buf[2];
  if (wheel >= 4 || dir >= 2) {
    Serial.printf("poly chunk reject: bad index w=%u d=%u\n", wheel, dir);
    return;
  }

  float k_steady;
  float kick_c[POLY_NCOEFS];
  float brake_c[POLY_NCOEFS];
  memcpy(&k_steady, buf + 4, 4);
  memcpy(kick_c,    buf + 8, POLY_NCOEFS * 4);
  memcpy(brake_c,   buf + 8 + POLY_NCOEFS * 4, POLY_NCOEFS * 4);

  if (!isfinite(k_steady) || k_steady < 0.0f) {
    Serial.printf("poly chunk reject: bad k_steady %.3f at w=%u d=%u\n",
                  k_steady, wheel, dir);
    return;
  }
  for (size_t i = 0; i < POLY_NCOEFS; ++i) {
    if (!isfinite(kick_c[i]) || !isfinite(brake_c[i])) {
      Serial.printf("poly chunk reject: non-finite at w=%u d=%u i=%u\n",
                    wheel, dir, static_cast<unsigned>(i));
      return;
    }
  }

  PerDirCoefs *target = (dir == 0) ? &g_poly[wheel].fwd : &g_poly[wheel].bwd;
  target->k_steady = k_steady;
  memcpy(target->kick.c,  kick_c,  POLY_NCOEFS * 4);
  memcpy(target->brake.c, brake_c, POLY_NCOEFS * 4);
  g_poly_chunks_received++;
}

// Per-wheel phase-aware motor command. Phase machine drives the (wheel, dir,
// phase) selection of polynomial cells; the polynomial in (s, t) emits
// p_norm ∈ ℝ which is then scaled by max_motor and clamped.
//
// Transitions:
//   IDLE   -> KICK   on sign 0 -> nonzero
//   STEADY -> BRAKE  on sign nonzero -> 0 (snapshot last_s_norm as s_pre)
//   *      -> KICK   on sign reversal (no explicit BRAKE; reverse drive
//                    dumps residual energy via H-bridge back-EMF braking)
//   KICK   -> STEADY on tau >= kick_dur_ms
//   BRAKE  -> IDLE   on tau >= brake_dur_ms
static void compute_motors(float vx, float vy, float wz, uint32_t now, int8_t out[4]) {
  float m[4] = {
    vx + vy + wz,
    vx - vy - wz,
    vx - vy + wz,
    vx + vy - wz,
  };
  float peak = fmaxf(fmaxf(fabsf(m[0]), fabsf(m[1])), fmaxf(fabsf(m[2]), fabsf(m[3])));
  float norm = (peak > 1.0f) ? (1.0f / peak) : 1.0f;
  float max_m = static_cast<float>(g_cfg.max_motor);

  for (int i = 0; i < 4; ++i) {
    float s = m[i] * norm;       // normalized command, ∈ [-1, 1]
    int8_t sign = (s > 0.0f) ? 1 : (s < 0.0f ? -1 : 0);
    MotorState &st = g_motor_state[i];

    if (st.last_sign == 0 && sign != 0) {
      st.phase = PH_KICK;
      st.phase_start_ms = now;
    } else if (st.last_sign != 0 && sign == 0 && st.phase != PH_BRAKE) {
      st.phase = PH_BRAKE;
      st.phase_start_ms = now;
      st.s_pre = st.last_s_norm;
      st.s_pre_sign = st.last_sign;
    } else if (st.last_sign != 0 && sign != 0 && sign != st.last_sign) {
      // Sign reversal: jump straight to KICK in the new direction. The
      // H-bridge dissipates the previous direction's residual energy when it
      // accelerates the wheel the other way, so an explicit BRAKE phase
      // would just delay the user-commanded reversal.
      st.phase = PH_KICK;
      st.phase_start_ms = now;
    }

    uint32_t tau = now - st.phase_start_ms;
    if (st.phase == PH_KICK && tau >= static_cast<uint32_t>(g_cfg.kick_dur_ms)) {
      st.phase = PH_STEADY;
      st.phase_start_ms = now;
      tau = 0;
    } else if (st.phase == PH_BRAKE && tau >= static_cast<uint32_t>(g_cfg.brake_dur_ms)) {
      st.phase = PH_IDLE;
      st.phase_start_ms = now;
      tau = 0;
    }

    int8_t out_i = 0;
    float t_sec = static_cast<float>(tau) / 1000.0f;
    switch (st.phase) {
      case PH_KICK: {
        const PerDirCoefs &pd = (sign > 0) ? g_poly[i].fwd : g_poly[i].bwd;
        float fk = eval_poly1d(pd.kick, t_sec);
        out_i = clamp_int8(static_cast<int>(s * fk * max_m));
        break;
      }
      case PH_STEADY: {
        const PerDirCoefs &pd = (sign > 0) ? g_poly[i].fwd : g_poly[i].bwd;
        out_i = clamp_int8(static_cast<int>(s * pd.k_steady * max_m));
        break;
      }
      case PH_BRAKE: {
        const PerDirCoefs &pd = (st.s_pre_sign > 0) ? g_poly[i].fwd : g_poly[i].bwd;
        // BRAKE drives `p = s_pre · f_b(t)`: the current commanded `s` is 0
        // (otherwise we wouldn't have entered BRAKE) so we use the snapshot
        // taken at STEADY → BRAKE.
        float fb = eval_poly1d(pd.brake, t_sec);
        out_i = clamp_int8(static_cast<int>(st.s_pre * fb * max_m));
        break;
      }
      case PH_IDLE:
      default:
        break;
    }

    out[i] = out_i * SIGN_M[i];
    st.last_sign = sign;
    st.last_s_norm = s;
    g_s_norm[i] = s;
  }
}

// Telemetry binary packet (69 bytes total, magic 0xD1):
//   [0]      0xD1
//   [1..4]   uint32 LE  millis()
//   [5..8]   float       gx_dps   (raw gyro X, deg/s; PC subtracts bias)
//   [9..12]  float       gy_dps   (raw gyro Y, deg/s)
//   [13..16] float       gz_dps   (raw gyro Z, deg/s)
//   [17..20] float       ax_g     (raw accel X, g; M5Unified default unit)
//   [21..24] float       ay_g     (raw accel Y, g)
//   [25..28] float       az_g     (raw accel Z, g)
//   [29..32] uint8[4]    phase    (PH_*)
//   [33..48] float[4]    s_pre    (normalized BRAKE snapshot per wheel)
//   [49..52] int8[4]     motor    (commanded I2C value)
//   [53..68] float[4]    s_norm   (current-tick normalized s, ∈ [-1, 1])
static void push_telemetry() {
  if (!g_cfg.tel_en) return;
  if (g_last_sender == IPAddress(0, 0, 0, 0) || g_last_sender_port == 0) return;

  uint8_t buf[80];
  size_t off = 0;
  buf[off++] = TEL_MAGIC;

  uint32_t t = millis();
  memcpy(buf + off, &t, 4); off += 4;

  float gx = 0.0f, gy = 0.0f, gz = 0.0f;
  float ax = 0.0f, ay = 0.0f, az = 0.0f;
  if (M5.Imu.update()) {
    M5.Imu.getGyro(&gx, &gy, &gz);
    M5.Imu.getAccel(&ax, &ay, &az);
  }
  memcpy(buf + off, &gx, 4); off += 4;
  memcpy(buf + off, &gy, 4); off += 4;
  memcpy(buf + off, &gz, 4); off += 4;
  memcpy(buf + off, &ax, 4); off += 4;
  memcpy(buf + off, &ay, 4); off += 4;
  memcpy(buf + off, &az, 4); off += 4;

  for (int i = 0; i < 4; ++i) buf[off++] = static_cast<uint8_t>(g_motor_state[i].phase);
  for (int i = 0; i < 4; ++i) {
    float v = g_motor_state[i].s_pre;
    memcpy(buf + off, &v, 4); off += 4;
  }
  for (int i = 0; i < 4; ++i) buf[off++] = static_cast<uint8_t>(g_motors[i]);
  for (int i = 0; i < 4; ++i) {
    memcpy(buf + off, &g_s_norm[i], 4); off += 4;
  }

  udp.beginPacket(g_last_sender, g_last_sender_port);
  udp.write(buf, off);
  udp.endPacket();
}

// Wire.end()+begin() alone does NOT recover a physically stuck I2C bus
// (SDA/SCL held low by a wedged slave). Drive SCL manually for up to 16
// pulses to clock out whatever bit the slave is holding, issue a manual
// STOP, then reinitialise the master.
static void recover_i2c_bus() {
  Wire.end();
  pinMode(PIN_SCL, OUTPUT_OPEN_DRAIN);
  pinMode(PIN_SDA, INPUT_PULLUP);
  digitalWrite(PIN_SCL, HIGH);
  for (int i = 0; i < 16 && digitalRead(PIN_SDA) == LOW; ++i) {
    digitalWrite(PIN_SCL, LOW);
    delayMicroseconds(5);
    digitalWrite(PIN_SCL, HIGH);
    delayMicroseconds(5);
  }
  // Manual STOP: SDA goes LOW->HIGH while SCL is HIGH.
  pinMode(PIN_SDA, OUTPUT_OPEN_DRAIN);
  digitalWrite(PIN_SDA, LOW);
  delayMicroseconds(5);
  digitalWrite(PIN_SCL, HIGH);
  delayMicroseconds(5);
  digitalWrite(PIN_SDA, HIGH);
  delayMicroseconds(5);
  Wire.begin(PIN_SDA, PIN_SCL, I2C_HZ);
  Serial.println("I2C bus reset (with clock pulses)");
}

static void probe_camera(CameraState &c, TwoWire &bus, bool count_recovery) {
  uint8_t got = bus.requestFrom((int)c.addr, (int)CAM_FRAME_SIZE);
  if (got != CAM_FRAME_SIZE) {
    while (bus.available()) bus.read();   // drain partial
    // Only count toward bus-recovery when the slave was at least *transiently*
    // active recently. A camera that has been missing for many probes already
    // is "established absent" -- continuing to count it would trigger endless
    // bus recoveries that disrupt the cameras that ARE present.
    bool was_active = c.present || c.fail_streak < MARK_NOT_PRESENT_AFTER;
    if (c.fail_streak < 0xFF) c.fail_streak++;
    if (c.fail_streak >= MARK_NOT_PRESENT_AFTER) {
      c.present = false;
    }
    if (was_active && count_recovery) g_probe_fails_since_last_ok++;
    return;
  }
  uint8_t buf[CAM_FRAME_SIZE] = {0};
  for (size_t i = 0; i < CAM_FRAME_SIZE; ++i) buf[i] = bus.read();
  c.fail_streak = 0;
  c.present = true;
  c.last_seen_ms = millis();
  c.ip[0] = buf[0];
  c.ip[1] = buf[1];
  c.ip[2] = buf[2];
  c.ip[3] = buf[3];
  c.http_port = (uint16_t)buf[4] | ((uint16_t)buf[5] << 8);
  c.camera_ok = buf[6] != 0;
  c.wifi_ok = buf[7] != 0;
  if (count_recovery) g_probe_fails_since_last_ok = 0;
}

static void emit_camera(JsonObject &cam, const char *role, const CameraState &c) {
  if (!c.present || !c.wifi_ok) {
    cam[role] = nullptr;
    return;
  }
  JsonObject o = cam[role].to<JsonObject>();
  char ip_buf[16];
  snprintf(ip_buf, sizeof(ip_buf), "%u.%u.%u.%u",
           c.ip[0], c.ip[1], c.ip[2], c.ip[3]);
  o["ip"] = ip_buf;
  o["port"] = c.http_port;
  o["ok"] = c.camera_ok;
}

static void push_camera_state() {
  if (g_last_sender == IPAddress(0, 0, 0, 0) || g_last_sender_port == 0) return;
  JsonDocument doc;
  JsonObject cam = doc["cam"].to<JsonObject>();
  emit_camera(cam, "left", g_cam_left);
  emit_camera(cam, "right", g_cam_right);
  emit_camera(cam, "fisheye", g_cam_fisheye);

  char buf[256];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  if (n == 0) return;
  udp.beginPacket(g_last_sender, g_last_sender_port);
  udp.write((const uint8_t *)buf, n);
  udp.endPacket();
}

static void send_camera_token(uint8_t addr, TwoWire &bus) {
  bus.beginTransmission((int)addr);
  bus.write(CAM_TOKEN_BYTE);
  // NACK from a missing camera is silently ignored; tokens are best-effort.
  bus.endTransmission();
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
  if (cfg["bdur"].is<int>()) {
    int bd = cfg["bdur"];
    if (bd < 0) bd = 0;
    if (bd > 2000) bd = 2000;
    g_cfg.brake_dur_ms = bd;
  }
  if (cfg["tel"].is<bool>()) {
    g_cfg.tel_en = cfg["tel"];
  }
  // Legacy scalar trim arrays `tf` / `tb` are redirected to per-(wheel, dir)
  // STEADY gain so existing teleop sliders still tune drive balance. The
  // `kf` / `kb` arrays are obsolete -- KICK is now the polynomial f_k(t)
  // pushed via 0xC0 chunks, no scalar-equivalent. Silently ignored if
  // present in older clients.
  if (cfg["tf"].is<JsonArrayConst>()) {
    JsonArrayConst arr = cfg["tf"];
    for (int i = 0; i < 4 && i < (int)arr.size(); ++i) {
      float v = clampf(arr[i] | 1.0f, 0.0f, 4.0f);
      g_poly[i].fwd.k_steady = v;
    }
  }
  if (cfg["tb"].is<JsonArrayConst>()) {
    JsonArrayConst arr = cfg["tb"];
    for (int i = 0; i < 4 && i < (int)arr.size(); ++i) {
      float v = clampf(arr[i] | 1.0f, 0.0f, 4.0f);
      g_poly[i].bwd.k_steady = v;
    }
  }
  g_configs_received++;
}

static void poll_udp() {
  int len = udp.parsePacket();
  if (len <= 0) return;

  static uint8_t buf[512];
  int n = udp.read(buf, sizeof(buf));
  if (n <= 0) return;

  // Binary polynomial cfg chunk -- magic byte distinguishes it from the
  // ASCII JSON envelope (which always starts with '{').
  if (n >= static_cast<int>(POLY_CHUNK_BYTES) && buf[0] == POLY_CHUNK_MAGIC) {
    apply_poly_chunk(buf, n);
    return;
  }

  // JSON path: motion or cfg envelope.
  if (n >= static_cast<int>(sizeof(buf))) n = sizeof(buf) - 1;
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
  g_last_sender_port = udp.remotePort();
  g_packets_received++;
}

static char phase_char(Phase p) {
  switch (p) {
    case PH_KICK:   return 'K';
    case PH_STEADY: return 'S';
    case PH_BRAKE:  return 'B';
    case PH_IDLE:
    default:        return 'I';
  }
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
  M5.Display.printf("m%4d%4d%4d%4d\n",
                    g_motors[0], g_motors[1], g_motors[2], g_motors[3]);

  // Per-wheel phase indicator (KSBI letters for FL FR RL RR). Yellow when
  // any wheel is currently in BRAKE (lets brake events flash visibly when
  // the user releases). Cyan when telemetry push is enabled.
  bool any_brake = false;
  for (int i = 0; i < 4; ++i) {
    if (g_motor_state[i].phase == PH_BRAKE) { any_brake = true; break; }
  }
  uint16_t ph_color = any_brake ? YELLOW : (g_cfg.tel_en ? CYAN : WHITE);
  M5.Display.setTextColor(ph_color, BLACK);
  M5.Display.printf("phase %c%c%c%c poly%4lu\n",
                    phase_char(g_motor_state[0].phase),
                    phase_char(g_motor_state[1].phase),
                    phase_char(g_motor_state[2].phase),
                    phase_char(g_motor_state[3].phase),
                    static_cast<unsigned long>(g_poly_chunks_received));
  M5.Display.setTextColor(WHITE, BLACK);

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
  // M5Unified may have already claimed I2C1 for Ex_I2C; tear down before
  // re-pinning to the Grove port pins.
  Wire1.end();
  delay(5);
  bool grove_ok = Wire1.begin(PIN_GROVE_SDA, PIN_GROVE_SCL, I2C_HZ);
  Serial.printf("Wire1 (Grove) begin sda=%d scl=%d hz=%lu -> %d\n",
                PIN_GROVE_SDA, PIN_GROVE_SCL, (unsigned long)I2C_HZ,
                (int)grove_ok);

  // Stop motors immediately at boot in case the HAT retained state.
  int8_t zero[4] = {0, 0, 0, 0};
  send_motors_i2c(zero);

  // Reclaim NVS used by the previous in-firmware atrim subsystem (now PC
  // side). Idempotent: noop if the namespace doesn't exist yet.
  {
    Preferences p;
    if (p.begin("atrim", false)) {
      p.clear();
      p.end();
    }
  }

  init_poly_defaults();

  Serial.begin(115200);
  connect_wifi();

  g_last_packet_ms = millis() - FAILSAFE_MS - 1;  // start in failsafe
  g_next_tick_ms = millis();
  g_next_tel_ms = millis();
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

  if (static_cast<int32_t>(now - g_next_tel_ms) >= 0) {
    g_next_tel_ms += TEL_PERIOD_MS;
    if (static_cast<int32_t>(now - g_next_tel_ms) > 200) {
      g_next_tel_ms = now + TEL_PERIOD_MS;  // catch up after a stall
    }
    push_telemetry();
  }

  if (static_cast<int32_t>(now - g_next_cam_tick_ms) >= 0) {
    g_next_cam_tick_ms = now + CAM_PROBE_INTERVAL_MS;
    if (g_probe_fails_since_last_ok >= RECOVER_BUS_AFTER) {
      recover_i2c_bus();
      g_probe_fails_since_last_ok = 0;
    }
    probe_camera(g_cam_left, Wire, true);
    probe_camera(g_cam_right, Wire, true);
    probe_camera(g_cam_fisheye, Wire1, false);
    push_camera_state();
  }

  if (static_cast<int32_t>(now - g_next_token_ms) >= 0) {
    g_next_token_ms = now + CAM_TOKEN_PERIOD_MS;
    if (static_cast<int32_t>(now - g_next_token_ms) > 200) {
      g_next_token_ms = now + CAM_TOKEN_PERIOD_MS;  // catch up after a stall
    }
    switch (g_token_target) {
      case 0:  send_camera_token(CAM_ADDR_LEFT, Wire); break;
      case 1:  send_camera_token(CAM_ADDR_RIGHT, Wire); break;
      default: send_camera_token(CAM_ADDR_FISHEYE, Wire1); break;
    }
    g_token_target = (g_token_target + 1) % 3;
  }

  update_lcd();
}
