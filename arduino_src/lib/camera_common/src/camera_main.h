#pragma once
#include <stdint.h>

struct CameraConfig {
  const char *role;             // e.g. "left", "right"
  const char *wifi_ssid;
  const char *wifi_password;
};

void camera_main_setup(const CameraConfig &cfg);
void camera_main_loop();
