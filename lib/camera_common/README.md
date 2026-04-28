# camera_common

Shared logic for the camera_node bring-up sketches (`src/camera_node_left`,
`src/camera_node_right`). The dispatching `.ino` calls `camera_main_setup`
once with a `CameraConfig` (role string + WiFi credentials + announce
parameters), then keeps `camera_main_loop` ticking.

What it provides:

- WiFi STA + reconnect.
- esp32-camera initialization (Timer Camera X pin map, QVGA JPEG).
- HTTP server on port 80 with `/jpg` and `/`.
- Periodic UDP broadcast self-announce on the announce port, JSON payload
  including `role`, `ip`, `http_port`, `jpg_path`, `camera_ok`, `seq`,
  `uptime_ms`.

flash.sh adds `--libraries lib` to arduino-cli so this directory is
discovered without sketchbook configuration.
