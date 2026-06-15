# camera_common

Shared logic for the camera_node sketches (`arduino_src/camera_node_front`
and the shelved `camera_node_{left,right,fisheye}`). The dispatching `.ino`
calls `camera_main_setup`
once with a `CameraConfig` (role string + WiFi credentials), then keeps
`camera_main_loop` ticking.

What it provides:

- WiFi STA + reconnect.
- esp32-camera initialization (Timer Camera X pin map, QVGA JPEG).
- HTTP server on port 80 with `/jpg` and `/`.
- An I2C slave status frame (IP / http_port / camera_ok / wifi_ok / vbat),
  read by the StickC master and relayed to the PC. This is the only path the
  camera's IP reaches the PC — there is no UDP self-announce.

flash.sh adds `--libraries arduino_src/lib` to arduino-cli so this directory
is discovered without sketchbook configuration.
