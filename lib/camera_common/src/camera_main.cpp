// Shared camera node logic: WiFi STA + esp32-camera + HTTP /jpg endpoint
// + periodic UDP broadcast self-announce. The dispatching sketch supplies
// a role string (the active rig uses "front"; "left"/"right"/"fisheye"
// remain for the shelved stereo/fisheye lines) and the WiFi/announce params.

#include "camera_main.h"

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_wifi.h"
#include <WebServer.h>
#include <Wire.h>
#include <string.h>
#include "esp_camera.h"
#include "esp_system.h"
#include "esp_task_wdt.h"

// Timer Camera X (OV3660) pin map.
#define PWDN_GPIO_NUM    -1
#define RESET_GPIO_NUM   15
#define XCLK_GPIO_NUM    27
#define SIOD_GPIO_NUM    25
#define SIOC_GPIO_NUM    23
#define Y9_GPIO_NUM      19
#define Y8_GPIO_NUM      36
#define Y7_GPIO_NUM      18
#define Y6_GPIO_NUM      39
#define Y5_GPIO_NUM       5
#define Y4_GPIO_NUM      34
#define Y3_GPIO_NUM      35
#define Y2_GPIO_NUM      32
#define VSYNC_GPIO_NUM   22
#define HREF_GPIO_NUM    26
#define PCLK_GPIO_NUM    21

static constexpr uint16_t HTTP_PORT = 80;

// I2C slave on the HY2.0 port pins of the Timer Camera X.
// (HY2.0 SDA=GPIO 4, SCL=GPIO 13; these are not used by the OV3660 sensor.)
// We use Wire1 (I2C1) so the camera SCCB on I2C0 is unaffected.
static constexpr int I2C_SDA_PIN = 4;
static constexpr int I2C_SCL_PIN = 13;
static constexpr uint32_t I2C_FREQ = 50000;
static constexpr uint8_t I2C_ADDR_FRONT = 0x40;
static constexpr uint8_t I2C_ADDR_LEFT = 0x40;
static constexpr uint8_t I2C_ADDR_RIGHT = 0x41;
static constexpr uint8_t I2C_ADDR_FISHEYE = 0x42;

// Map role string to the I2C slave address. The active rig is a single
// front monocular camera (front=0x40). The legacy stereo/fisheye roles are
// retained so older sketches still flash; unknown roles fall back to the
// front address.
static uint8_t addr_for_role(const char *role) {
  if (strcmp(role, "left") == 0) return I2C_ADDR_LEFT;
  if (strcmp(role, "right") == 0) return I2C_ADDR_RIGHT;
  if (strcmp(role, "fisheye") == 0) return I2C_ADDR_FISHEYE;
  return I2C_ADDR_FRONT;
}
// Slave response frame layout (10 bytes), read by the StickC master:
//   [0..3] IPv4 octets   [4..5] http_port (LE)   [6] camera_ok   [7] wifi_ok
//   [8..9] vbat_mv (LE)  -- battery voltage at the JST-PH input, mV
static constexpr size_t I2C_RESPONSE_SIZE = 10;
static volatile uint8_t g_i2c_response[I2C_RESPONSE_SIZE] = {0};

// Timer Camera X / F battery sense.
//
// Per the official schematic Sch_M5TimerCAM.pdf and M5Stack's official
// Power_Class.cpp / Power_Class.h:
//
//   J4 LiPo --+-- VBAT_IN (battery direct)
//             +-- TP4057 BAT pin (charger)
//             +-- FET3 (PMOS) -- VBAT --+-- R28 (1.37K) -- GPIO 38
//                                       +-- D6 -- VSYS_VIN
//                                       |
//                                       +-- R29 (2.67K) -- GND
//
// FET3 is the soft-power PMOS gating the battery rail to the system. It is
// only ON when POWER_HOLD_PIN (GPIO 33) is held HIGH. The divider that the
// ADC reads is on VBAT (downstream of FET3), NOT on VBAT_IN. So without
// asserting POWER_HOLD, the divider's input floats and the ADC saturates at
// its ~140 mV lower-bound noise floor regardless of charger / battery state.
// The earlier code missed this -- it never drove GPIO 33, so the camera ran
// fine off Grove HY2.0 5V (which feeds VSYS_VIN directly via D8) but the
// battery sense was permanently stuck at ~0.28 V (= 140 mV * 2).
//
// Divider ratio: 2.67 / (1.37 + 2.67) = 0.661, so VBAT = V_GPIO38 / 0.661.
// Done as integer math (mv * 404 / 267) to keep the path on int32.
static constexpr int BAT_ADC_PIN = 38;
static constexpr int POWER_HOLD_PIN = 33;
static constexpr uint32_t BAT_DIV_NUM = 404;
static constexpr uint32_t BAT_DIV_DEN = 267;

static CameraConfig g_cfg = {};
static WiFiUDP g_udp;
static WebServer g_server(HTTP_PORT);

static char g_device_id[32] = {0};   // "camera_<role>_<mac3>"
static uint32_t g_seq = 0;
static uint32_t g_next_announce_ms = 0;
static bool g_camera_ok = false;

// Self-heal: every HEALTH_INTERVAL_MS, take a throwaway frame to confirm the
// camera DMA pipeline is alive. After FB_FAIL_LIMIT consecutive failures we
// reboot. The hardware task watchdog (TASK_WDT_TIMEOUT_S) covers the case
// where esp_camera_fb_get() blocks the loop indefinitely.
static constexpr uint32_t HEALTH_INTERVAL_MS = 5000;
static constexpr uint8_t  FB_FAIL_LIMIT = 6;     // ~30s of failures
static constexpr uint32_t TASK_WDT_TIMEOUT_S = 30;
static uint32_t g_next_health_ms = 0;
static uint8_t  g_consecutive_fb_fails = 0;

// Wire1 (I2C slave) self-heal. esp_camera_deinit/init during runtime
// framesize changes has been observed to wedge the I2C slave peripheral so
// the StickC master can no longer probe us, even though HTTP keeps working.
// We track the timestamp of the last on_i2c_request callback; if no master
// has polled in I2C_SLAVE_HEAL_MS we tear Wire1 down and bring it back up.
static constexpr uint32_t I2C_SLAVE_HEAL_MS = 3000;
static volatile uint32_t g_last_i2c_request_ms = 0;

// MJPEG flow control. The StickC master sends a 1-byte "go" write to the
// front camera (0x40) at ~20 Hz. This token-paced design dates from the
// stereo era, where alternating tokens to 0x40 / 0x41 kept the two cameras
// from transmitting a frame at the same instant (airtime contention on
// shared 2.4 GHz). With a single camera there is no contention to avoid, but
// the token still bounds per-frame airtime. If no token arrives within
// TOKEN_TIMEOUT_MS the stream loop free-runs, so a dead StickC does not
// freeze the camera.
//
// Peak-byte cap: skip frames whose JPEG is larger than PEAK_BYTES_LIMIT to
// avoid hogging airtime during high-entropy scenes. After MAX_SKIP_STREAK
// consecutive skips we send the next frame anyway, so heavy-motion scenes
// still get at least 1 update per ~330 ms.
static constexpr uint32_t TOKEN_TIMEOUT_MS = 200;
static constexpr size_t   PEAK_BYTES_LIMIT = 12000;
static constexpr uint8_t  MAX_SKIP_STREAK = 3;
static volatile bool g_send_token = false;

static const char *reset_reason_name(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON:  return "POWERON";
    case ESP_RST_EXT:      return "EXT";
    case ESP_RST_SW:       return "SW";
    case ESP_RST_PANIC:    return "PANIC";
    case ESP_RST_INT_WDT:  return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT:      return "WDT";
    case ESP_RST_DEEPSLEEP:return "DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_SDIO:     return "SDIO";
    default:               return "UNKNOWN";
  }
}

static void make_device_id(const char *role) {
  uint8_t mac[6] = {0};
  WiFi.macAddress(mac);
  snprintf(g_device_id, sizeof(g_device_id), "camera_%s_%02x%02x%02x",
           role, mac[3], mac[4], mac[5]);
}

static bool connect_wifi(uint32_t timeout_ms) {
  WiFi.mode(WIFI_STA);
  WiFi.persistent(true);
  WiFi.setAutoReconnect(true);
  WiFi.begin(g_cfg.wifi_ssid, g_cfg.wifi_password);
  Serial.printf("WiFi: connecting to %s\n", g_cfg.wifi_ssid);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > timeout_ms) {
      Serial.println("WiFi: connect timeout");
      return false;
    }
    delay(200);
    Serial.print('.');
  }
  Serial.println();
  Serial.print("WiFi: connected, IP=");
  Serial.println(WiFi.localIP());
  // Disable WiFi modem-sleep so TX is not deferred to DTIM beacon intervals.
  // Default WIFI_PS_MIN_MODEM batches outgoing TCP data into ~1s bursts which
  // makes the MJPEG stream visually update only once per second on the PC.
  esp_wifi_set_ps(WIFI_PS_NONE);
  Serial.println("WiFi: power save disabled (WIFI_PS_NONE)");
  return true;
}

static IPAddress broadcast_address() {
  IPAddress ip = WiFi.localIP();
  IPAddress mask = WiFi.subnetMask();
  IPAddress bcast;
  for (int i = 0; i < 4; i++) {
    bcast[i] = (ip[i] & mask[i]) | (~mask[i] & 0xFF);
  }
  return bcast;
}

static void send_announce() {
  IPAddress bcast = broadcast_address();
  IPAddress ip = WiFi.localIP();
  char buf[220];
  int n = snprintf(
      buf, sizeof(buf),
      "{\"id\":\"%s\",\"role\":\"%s\",\"ip\":\"%u.%u.%u.%u\","
      "\"http_port\":%u,\"jpg_path\":\"/jpg\",\"camera_ok\":%s,"
      "\"seq\":%lu,\"uptime_ms\":%lu}",
      g_device_id, g_cfg.role, ip[0], ip[1], ip[2], ip[3],
      (unsigned)HTTP_PORT, g_camera_ok ? "true" : "false",
      (unsigned long)g_seq, (unsigned long)millis());
  if (n <= 0) return;
  g_udp.beginPacket(bcast, g_cfg.announce_port);
  g_udp.write((const uint8_t *)buf, n);
  g_udp.endPacket();
  g_seq++;
}

static framesize_t g_current_framesize = FRAMESIZE_QVGA;
static int g_current_quality = 30;

static const char *framesize_name(framesize_t fs);
static void on_i2c_request();
static void on_i2c_receive(int len);
static void update_i2c_response();
static void check_i2c_slave_health();

static void fill_camera_config(camera_config_t &config, framesize_t fs, int quality) {
  config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  // 8MHz: avoids documented 2.4GHz WiFi interference that 10/20MHz XCLK
  // produces on ESP32. See espressif/arduino-esp32 #5834.
  config.xclk_freq_hz = 8000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = fs;
  config.jpeg_quality = quality;
  config.fb_count = 2;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;
}

static bool camera_init() {
  camera_config_t config;
  fill_camera_config(config, g_current_framesize, g_current_quality);
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("camera init failed: 0x%x\n", err);
    return false;
  }
  // OV3660 default mounting on Timer Camera X yields a horizontally mirrored
  // image (selfie convention). Flip it so forward-facing scenes render the
  // way the operator expects.
  sensor_t *s = esp_camera_sensor_get();
  if (s != nullptr) {
    s->set_hmirror(s, 1);
  }
  Serial.println("camera initialized");
  return true;
}

// Tear down esp32-camera and bring it back up at a new framesize. This is the
// only safe way to change frame size at runtime: DMA buffers are sized at
// esp_camera_init() time from the initial framesize, so calling
// sensor_t::set_framesize alone risks DMA writing past the allocated buffer
// when stepping up to a larger resolution.
static bool camera_reinit(framesize_t fs, int quality) {
  esp_camera_deinit();
  g_current_framesize = fs;
  g_current_quality = quality;
  camera_config_t config;
  fill_camera_config(config, fs, quality);
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("camera reinit failed: 0x%x\n", err);
    g_camera_ok = false;
    return false;
  }
  sensor_t *s = esp_camera_sensor_get();
  if (s != nullptr) {
    s->set_hmirror(s, 1);
  }
  g_camera_ok = true;
  // esp_camera_deinit/init can wedge the I2C slave peripheral on this SoC;
  // proactively re-establish Wire1 so the StickC master probe never has to
  // wait the 10s heal timeout after a framesize change.
  Wire1.end();
  delay(5);
  uint8_t addr = addr_for_role(g_cfg.role);
  if (Wire1.begin(addr, I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ)) {
    Wire1.onRequest(on_i2c_request);
    Wire1.onReceive(on_i2c_receive);
  } else {
    Serial.printf("Wire1 post-reinit failed (addr=0x%02x)\n", addr);
  }
  g_last_i2c_request_ms = millis();
  Serial.printf("camera reinitialized: fs=%d (%s) q=%d\n",
                (int)fs, framesize_name(fs), quality);
  return true;
}

static const char *framesize_name(framesize_t fs) {
  switch (fs) {
    case FRAMESIZE_96X96:  return "96x96";
    case FRAMESIZE_QQVGA:  return "QQVGA";
    case FRAMESIZE_QCIF:   return "QCIF";
    case FRAMESIZE_HQVGA:  return "HQVGA";
    case FRAMESIZE_240X240:return "240x240";
    case FRAMESIZE_QVGA:   return "QVGA";
    case FRAMESIZE_CIF:    return "CIF";
    case FRAMESIZE_HVGA:   return "HVGA";
    case FRAMESIZE_VGA:    return "VGA";
    case FRAMESIZE_SVGA:   return "SVGA";
    case FRAMESIZE_XGA:    return "XGA";
    case FRAMESIZE_HD:     return "HD";
    case FRAMESIZE_SXGA:   return "SXGA";
    case FRAMESIZE_UXGA:   return "UXGA";
    case FRAMESIZE_FHD:    return "FHD";
    case FRAMESIZE_QXGA:   return "QXGA";
    default:               return "?";
  }
}

static bool framesize_allowed(int fs) {
  switch (fs) {
    case FRAMESIZE_96X96:
    case FRAMESIZE_QQVGA: case FRAMESIZE_QCIF: case FRAMESIZE_HQVGA:
    case FRAMESIZE_240X240: case FRAMESIZE_QVGA: case FRAMESIZE_CIF:
    case FRAMESIZE_HVGA: case FRAMESIZE_VGA: case FRAMESIZE_SVGA:
    case FRAMESIZE_XGA: case FRAMESIZE_HD: case FRAMESIZE_SXGA:
    case FRAMESIZE_UXGA:
      return true;
    default:
      return false;
  }
}

static void handle_control() {
  // Validate args up front so we never half-apply a change.
  bool want_fs = g_server.hasArg("fs");
  bool want_q  = g_server.hasArg("q");
  framesize_t new_fs = g_current_framesize;
  int new_q = g_current_quality;
  if (want_fs) {
    int fs = g_server.arg("fs").toInt();
    if (!framesize_allowed(fs)) {
      g_server.send(400, "text/plain", "fs out of range");
      return;
    }
    new_fs = (framesize_t)fs;
  }
  if (want_q) {
    int q = g_server.arg("q").toInt();
    if (q < 4 || q > 63) {
      g_server.send(400, "text/plain", "q out of range [4,63]");
      return;
    }
    new_q = q;
  }

  bool changed = false;
  if (want_fs && new_fs != g_current_framesize) {
    // Framesize change requires full deinit/reinit; a bare set_framesize
    // would mismatch the DMA buffer size and corrupt memory on larger sizes.
    if (!camera_reinit(new_fs, new_q)) {
      g_server.send(500, "text/plain", "camera reinit failed");
      return;
    }
    changed = true;
  } else if (want_q && new_q != g_current_quality) {
    // Quality-only change is safe to apply on the live sensor.
    sensor_t *s = esp_camera_sensor_get();
    if (s == nullptr) {
      g_server.send(503, "text/plain", "sensor unavailable");
      return;
    }
    if (s->set_quality(s, new_q) != 0) {
      g_server.send(500, "text/plain", "set_quality failed");
      return;
    }
    g_current_quality = new_q;
    changed = true;
  }

  char buf[96];
  snprintf(buf, sizeof(buf), "fs=%d (%s) q=%d changed=%d\n",
           (int)g_current_framesize, framesize_name(g_current_framesize),
           g_current_quality, changed ? 1 : 0);
  g_server.send(200, "text/plain", buf);
}

static void handle_jpg() {
  if (!g_camera_ok) {
    g_server.send(503, "text/plain", "camera not initialized");
    return;
  }
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    g_server.send(500, "text/plain", "capture failed");
    return;
  }
  g_server.setContentLength(fb->len);
  g_server.sendHeader("Content-Type", "image/jpeg");
  g_server.sendHeader("Cache-Control", "no-store");
  g_server.send(200, "image/jpeg", "");
  WiFiClient client = g_server.client();
  client.write(fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

// MJPEG multipart streaming endpoint. One persistent TCP connection per
// client; frames sent back-to-back with --frame boundaries. Saves the
// HTTP/TCP handshake overhead of polling /jpg per frame.
//
// While inside this handler the main loop does not run, so we manually feed
// the task watchdog and periodically run camera/Wire1 health checks. /control
// is unreachable while a stream is active -- the client must close the
// connection before sending /control.
static void handle_stream() {
  if (!g_camera_ok) {
    g_server.send(503, "text/plain", "camera not initialized");
    return;
  }
  WiFiClient client = g_server.client();
  if (!client) return;
  client.setNoDelay(true);
  static const char *resp_hdr =
      "HTTP/1.1 200 OK\r\n"
      "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
      "Cache-Control: no-store\r\n"
      "Connection: close\r\n"
      "Access-Control-Allow-Origin: *\r\n"
      "\r\n";
  client.write((const uint8_t *)resp_hdr, strlen(resp_hdr));

  uint32_t last_health_ms = millis();
  uint8_t skip_streak = 0;
  while (client.connected() && g_camera_ok) {
    // Wait for the StickC's "go" token, or fall through after TOKEN_TIMEOUT_MS
    // so a dead/silent master does not freeze the stream entirely.
    uint32_t wait_start = millis();
    while (!g_send_token && client.connected() &&
           (uint32_t)(millis() - wait_start) < TOKEN_TIMEOUT_MS) {
      esp_task_wdt_reset();
      delay(2);
    }
    g_send_token = false;
    if (!client.connected()) break;

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
      esp_task_wdt_reset();
      delay(10);
      continue;
    }
    // Peak-byte cap: drop oversized frames to relieve airtime contention
    // when scene complexity spikes. Force a send after MAX_SKIP_STREAK
    // consecutive skips so heavy-motion scenes don't go fully silent.
    if (fb->len > PEAK_BYTES_LIMIT && skip_streak < MAX_SKIP_STREAK) {
      skip_streak++;
      esp_camera_fb_return(fb);
      esp_task_wdt_reset();
      continue;
    }
    skip_streak = 0;

    char hdr[96];
    int n = snprintf(hdr, sizeof(hdr),
                     "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                     (unsigned)fb->len);
    client.write((const uint8_t *)hdr, n);
    client.write(fb->buf, fb->len);
    client.write((const uint8_t *)"\r\n", 2);
    // Force lwIP to flush the TCP send buffer NOW so the frame goes out as
    // its own segment instead of being coalesced with later frames.
    client.flush();
    esp_camera_fb_return(fb);

    esp_task_wdt_reset();
    uint32_t now = millis();
    if ((uint32_t)(now - last_health_ms) > 1000) {
      last_health_ms = now;
      check_i2c_slave_health();
      // Refresh i2c response so StickC sees current camera_ok during stream.
      update_i2c_response();
    }
  }
  client.stop();
}

static void update_i2c_response() {
  IPAddress ip = WiFi.localIP();
  bool wifi_ok = WiFi.status() == WL_CONNECTED;
  // Take 8 samples and average to suppress single-sample jitter. The
  // multiplier converts the divider midpoint reading back to VBAT_IN: see
  // BAT_DIV_NUM / BAT_DIV_DEN comment block.
  uint32_t adc_sum = 0;
  for (int i = 0; i < 8; ++i) {
    adc_sum += analogReadMilliVolts(BAT_ADC_PIN);
  }
  uint32_t mv_pin = adc_sum / 8;
  uint32_t vbat_mv = mv_pin * BAT_DIV_NUM / BAT_DIV_DEN;
  if (vbat_mv > 0xFFFF) vbat_mv = 0xFFFF;
  noInterrupts();
  g_i2c_response[0] = ip[0];
  g_i2c_response[1] = ip[1];
  g_i2c_response[2] = ip[2];
  g_i2c_response[3] = ip[3];
  g_i2c_response[4] = (uint8_t)(HTTP_PORT & 0xFF);
  g_i2c_response[5] = (uint8_t)((HTTP_PORT >> 8) & 0xFF);
  g_i2c_response[6] = g_camera_ok ? 1 : 0;
  g_i2c_response[7] = wifi_ok ? 1 : 0;
  g_i2c_response[8] = (uint8_t)(vbat_mv & 0xFF);
  g_i2c_response[9] = (uint8_t)((vbat_mv >> 8) & 0xFF);
  interrupts();
}

static void on_i2c_request() {
  Wire1.write((const uint8_t *)g_i2c_response, I2C_RESPONSE_SIZE);
  g_last_i2c_request_ms = millis();
}

// Master write to our address: any payload counts as a "go ahead and send a
// frame" token. Drains the FIFO so the next write is not appended to leftover
// bytes.
static void on_i2c_receive(int len) {
  (void)len;
  while (Wire1.available()) (void)Wire1.read();
  g_send_token = true;
  g_last_i2c_request_ms = millis();
}

static void check_i2c_slave_health() {
  uint32_t now = millis();
  if ((uint32_t)(now - g_last_i2c_request_ms) < I2C_SLAVE_HEAL_MS) return;
  Serial.println("i2c slave: no master requests in 10s, reinitialising Wire1");
  Wire1.end();
  delay(10);
  uint8_t addr = addr_for_role(g_cfg.role);
  if (!Wire1.begin(addr, I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ)) {
    Serial.printf("Wire1 reinit failed (addr=0x%02x)\n", addr);
  } else {
    Wire1.onRequest(on_i2c_request);
    Wire1.onReceive(on_i2c_receive);
    Serial.printf("Wire1 reinitialised (addr=0x%02x)\n", addr);
  }
  g_last_i2c_request_ms = now;
}

static void i2c_slave_init(const char *role) {
  uint8_t addr = addr_for_role(role);
  if (!Wire1.begin((uint8_t)addr, I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ)) {
    Serial.printf("Wire1 slave init failed (addr=0x%02x)\n", addr);
    return;
  }
  Wire1.onRequest(on_i2c_request);
  Wire1.onReceive(on_i2c_receive);
  Serial.printf("I2C slave on Wire1 sda=%d scl=%d addr=0x%02x\n",
                I2C_SDA_PIN, I2C_SCL_PIN, addr);
}

static void check_camera_health() {
  uint32_t now = millis();
  if ((int32_t)(now - g_next_health_ms) < 0) return;
  g_next_health_ms = now + HEALTH_INTERVAL_MS;

  if (!g_camera_ok) {
    if (++g_consecutive_fb_fails >= FB_FAIL_LIMIT) {
      Serial.println("camera never came up; restarting");
      delay(100);
      ESP.restart();
    }
    return;
  }
  camera_fb_t *fb = esp_camera_fb_get();
  if (fb != nullptr) {
    esp_camera_fb_return(fb);
    g_consecutive_fb_fails = 0;
    return;
  }
  if (++g_consecutive_fb_fails >= FB_FAIL_LIMIT) {
    Serial.printf("camera unhealthy (%u consecutive fb_get fails); restarting\n",
                  (unsigned)g_consecutive_fb_fails);
    delay(100);
    ESP.restart();
  } else {
    Serial.printf("camera health: fb_get failed (%u/%u)\n",
                  (unsigned)g_consecutive_fb_fails, (unsigned)FB_FAIL_LIMIT);
  }
}

static void handle_root() {
  char buf[200];
  IPAddress ip = WiFi.localIP();
  snprintf(buf, sizeof(buf),
           "id=%s role=%s ip=%u.%u.%u.%u camera_ok=%d uptime_ms=%lu\n",
           g_device_id, g_cfg.role, ip[0], ip[1], ip[2], ip[3],
           (int)g_camera_ok, (unsigned long)millis());
  g_server.send(200, "text/plain", buf);
}

void camera_main_setup(const CameraConfig &cfg) {
  Serial.begin(115200);
  delay(100);

  // Drive POWER_HOLD HIGH first so the soft-power PMOS (FET3) latches the
  // battery rail to the system. Without this:
  //   - the camera still runs whenever USB or Grove HY2.0 5V is supplying
  //     VSYS_VIN (so we never noticed in normal use), but
  //   - the battery-sense divider on VBAT (post-FET3) reads ~0 V and the
  //     ADC saturates at its ~140 mV floor regardless of charge state.
  // Matches the M5Stack-official Power_Class::begin() initialisation order.
  pinMode(POWER_HOLD_PIN, OUTPUT);
  digitalWrite(POWER_HOLD_PIN, HIGH);

  g_cfg = cfg;
  make_device_id(cfg.role);
  esp_reset_reason_t rr = esp_reset_reason();
  Serial.printf("device_id=%s role=%s reset_reason=%d (%s)\n",
                g_device_id, cfg.role, (int)rr, reset_reason_name(rr));

  g_camera_ok = camera_init();

  if (!connect_wifi(20000)) {
    Serial.println("Restarting in 5s...");
    delay(5000);
    ESP.restart();
  }

  g_udp.begin(g_cfg.announce_port);

  g_server.on("/", handle_root);
  g_server.on("/jpg", handle_jpg);
  g_server.on("/stream", handle_stream);
  g_server.on("/control", handle_control);
  g_server.begin();
  Serial.printf("HTTP server on :%u (/jpg)\n", (unsigned)HTTP_PORT);

  update_i2c_response();
  i2c_slave_init(cfg.role);
  g_last_i2c_request_ms = millis();

  g_next_announce_ms = millis();
  g_next_health_ms = millis() + HEALTH_INTERVAL_MS;

#if ESP_IDF_VERSION_MAJOR >= 5
  // Arduino-ESP32 v3.x already initialised the WDT with default 5s; override
  // with our longer timeout via reconfigure() so esp_camera_fb_get() inside
  // /jpg handlers does not trigger a false panic.
  esp_task_wdt_config_t wdt_config = {
      .timeout_ms = TASK_WDT_TIMEOUT_S * 1000,
      .idle_core_mask = 0,
      .trigger_panic = true,
  };
  esp_task_wdt_reconfigure(&wdt_config);
#else
  esp_task_wdt_init(TASK_WDT_TIMEOUT_S, /*panic=*/true);
#endif
  esp_task_wdt_add(nullptr);
  Serial.printf("task watchdog armed: %us\n", (unsigned)TASK_WDT_TIMEOUT_S);
}

void camera_main_loop() {
  esp_task_wdt_reset();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi: link lost, reconnecting");
    if (!connect_wifi(20000)) {
      delay(2000);
      return;
    }
  }
  g_server.handleClient();
  uint32_t now = millis();
  if ((int32_t)(now - g_next_announce_ms) >= 0) {
    update_i2c_response();
    send_announce();
    g_next_announce_ms = now + g_cfg.announce_interval_ms;
  }
  check_camera_health();
  check_i2c_slave_health();
  delay(2);
}
