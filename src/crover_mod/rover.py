"""High-level facade for a single RoverC.

`Rover` wraps the UDP control client, camera discovery, and the MJPEG stream
behind a small surface:

    config = Config("config.json")
    rover = Rover("192.168.1.123", config)   # host = StickC Plus2 IP
    rover.move((1.0, 0.0), turn=0.0)         # drive forward at full output
    img = rover.get_camera()                 # latest front-camera frame as a BGR ndarray
    rover.stop()
    rover.close()

Camera discovery goes through the StickC only: the StickC probes the camera on
its I2C bus and relays the camera's IP / HTTP port as camera state, so callers
never deal with camera addresses. Because the only camera entry comes from this
rover's own StickC relay, the entry is rover-specific even when several rovers
share the LAN. `get_camera()` returns the most recent decoded frame (or None
until one arrives) and lazily opens the stream on first use.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from camera import CameraRegistry, CameraStream, set_camera_params
from roverc import RoverCClient

if TYPE_CHECKING:
    import numpy as np

    from config import Config


class Rover:
    def __init__(self, host: str, config: Config) -> None:
        self.host = host
        self.config = config
        self.port = config.port
        # Camera framesize / JPEG quality from config.json's camera section,
        # pushed to the camera once its stream first opens. None leaves the
        # firmware default untouched.
        self._cam_framesize = config.camera_framesize
        self._cam_quality = config.camera_quality
        self._registry = CameraRegistry()
        self._client = RoverCClient(host, self.port, camera_registry=self._registry)
        self._streams: dict[str, CameraStream] = {}
        self._closed = False
        # The firmware failsafe is driven by a periodic liveness heartbeat, not
        # by the motion command: a setpoint sent via move() is held firmware-side
        # until the next move()/stop(), while this background thread keeps the
        # heartbeat alive at 20 Hz (well inside the ~200 ms failsafe). If this
        # thread or the link dies, the heartbeat lapses and the motors stop.
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="RoverHeartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    # -- motion ----------------------------------------------------------

    def move(self, direction: tuple[float, float] | np.ndarray, turn: float) -> None:
        """Set the motion setpoint: translate along `direction = (x, y)`
        (x forward, y strafe-left; a 2-tuple or length-2 numpy array) while
        rotating at `turn` (> 0 = CCW). Both can be nonzero at once, so the
        rover arcs. `direction`'s length and `turn` are fractions of full motor
        output: a norm of 1 (or more, capped to 1) is full output, which the
        firmware mecanum-mixes and scales by config motor.max_motor. Returns
        immediately; the setpoint is sent once and held firmware-side (kept
        alive by the background heartbeat) until the next move()/stop()."""
        vx, vy = float(direction[0]), float(direction[1])
        norm = math.hypot(vx, vy)
        if norm > 1.0:
            vx, vy = vx / norm, vy / norm
        # Firmware mixing makes wz > 0 spin clockwise; negate so the API's
        # turn > 0 means CCW (standard right-hand convention).
        wz = -max(-1.0, min(1.0, float(turn)))
        self._client.send_motion(vx, vy, wz)

    def stop(self) -> None:
        self._client.send_motion(0.0, 0.0, 0.0)

    def _heartbeat_loop(self) -> None:
        while not self._closed:
            self._client.send_heartbeat()
            time.sleep(0.05)  # 20 Hz, well inside the ~200 ms firmware failsafe

    def push_motor_config(self) -> int:
        """Push the motor coefficient table from config to the firmware.
        Returns the number of chunk transmissions."""
        from coefs import from_config, push_to_firmware

        coefs = from_config(self.config.raw)
        return push_to_firmware(
            coefs, self._client.send_poly_chunk, self._client.send_config_dict
        )

    # -- camera ----------------------------------------------------------

    def get_camera(self, role: str = "front"):
        """Latest frame for `role` as a BGR ndarray, or None if none yet."""
        stream = self._stream_for(role)
        if stream is None:
            return None
        jpeg, _count, _ts, _err, _errors = stream.latest()
        if jpeg is None:
            return None
        import cv2
        import numpy as np

        return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)

    def _stream_for(self, role: str) -> CameraStream | None:
        stream = self._streams.get(role)
        if stream is not None:
            return stream
        info = self._registry.latest(role)
        if info is None:
            return None
        # Apply the configured framesize / quality before streaming so the very
        # first frame already matches config.json.
        if self._cam_framesize is not None or self._cam_quality is not None:
            set_camera_params(
                info, framesize=self._cam_framesize, quality=self._cam_quality
            )
        # Force the MJPEG /stream endpoint (the relayed entry may carry a
        # single-shot /jpg default).
        stream = CameraStream(replace(info, jpg_path="/stream"))
        stream.start()
        self._streams[role] = stream
        return stream

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._closed = True
        self._heartbeat_thread.join(timeout=1.0)
        for stream in self._streams.values():
            stream.stop()
        self._client.close()

    def __enter__(self) -> Rover:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
