// Thin dispatch sketch: fisheye camera (Timer Camera F, OV3660 + ~150° lens).
// Logic lives in libraries/camera_common. Target board: M5Stack Timer Camera
// (FQBN esp32:esp32:m5stack_timer_cam) -- the F variant shares the X
// firmware footprint, only the lens differs.

#include <camera_main.h>

#include "secrets.h"

void setup() {
  static const CameraConfig cfg = {
      "fisheye",
      WIFI_SSID,
      WIFI_PASSWORD,
  };
  camera_main_setup(cfg);
}

void loop() { camera_main_loop(); }
