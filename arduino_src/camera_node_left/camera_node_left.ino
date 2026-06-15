// Thin dispatch sketch: left camera. Logic lives in libraries/camera_common.
// Target board: M5Stack Timer Camera X (FQBN esp32:esp32:m5stack_timer_cam).

#include <camera_main.h>

#include "secrets.h"

void setup() {
  static const CameraConfig cfg = {
      "left",
      WIFI_SSID,
      WIFI_PASSWORD,
  };
  camera_main_setup(cfg);
}

void loop() { camera_main_loop(); }
