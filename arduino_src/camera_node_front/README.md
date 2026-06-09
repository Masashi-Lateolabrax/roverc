# camera_node_front

Thin dispatch sketch for the single front-facing Timer Camera X (monocular,
no stereo, no fisheye). All logic lives in `libraries/camera_common`; this
sketch only fixes the role string. The `front` role maps to I2C slave
address 0x40 on the RoverC HAT bus.

```bash
./flash.sh arduino_src/camera_node_front
```
