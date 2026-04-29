# camera_node_fisheye

Thin dispatch sketch for the fisheye Timer Camera F. All logic lives in
`libraries/camera_common`; this sketch only fixes the role string. The
fisheye node binds I2C slave address 0x42 (left=0x40, right=0x41).

See `docs/fisheye_vio_roadmap.md` for the wider plan (mast mount, single
fisheye + IMU VIO on PC).

```bash
./flash.sh src/camera_node_fisheye
```
