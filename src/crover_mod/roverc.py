"""UDP client for the RoverC StickC Plus2 server.

Wire protocol on a single UDP socket (PC <-> StickC):

PC -> StickC
- heartbeat (binary, magic 0xA0, 1 B): liveness only. The firmware failsafe is
  driven SOLELY by heartbeat arrival -- if none lands for FAILSAFE_MS the motors
  are zeroed. Motion packets do NOT refresh it, so this must be sent periodically
  (well inside the failsafe window) for the rover to act on any setpoint.
- motion packet (JSON): {"t": ..., "vx": ..., "vy": ..., "wz": ...}. Sets the
  setpoint, which the firmware holds until the next motion packet -- send once
  per change, not continuously.
- config packet (JSON): {"cfg": {"mx": ..., "tel": ...}}
- polynomial chunk (binary, magic 0xC0, 60 B): per-cell coefficients and
  phase durations -- see `coefs.py`

StickC -> PC
- camera state (JSON, ~1 Hz): {"cam": {"left": {"ip","port","ok","vbat_mv"}|null, ...}}
- telemetry (binary, magic 0xD2, 25 Hz, 73 B): see `telemetry.py`

The receive loop demultiplexes binary telemetry to `on_telemetry` and JSON
camera-state into the optional `CameraRegistry`. Both are off by default; if
neither is supplied, the rx thread isn't started.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable

from camera import CameraRegistry

# 1-byte liveness heartbeat the firmware uses to drive its failsafe (see the
# module docstring). Must match HEARTBEAT_MAGIC in roverc_server.ino.
HEARTBEAT_MAGIC = b"\xa0"


class RoverCClient:
    def __init__(
        self,
        host: str,
        port: int,
        camera_registry: CameraRegistry | None = None,
        on_telemetry: Callable[[bytes], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._addr = (host, port)
        self._registry = camera_registry
        self._on_telemetry = on_telemetry
        self._stop = threading.Event()
        self._rx_thread: threading.Thread | None = None
        if camera_registry is not None or on_telemetry is not None:
            self._sock.settimeout(0.5)
            self._rx_thread = threading.Thread(
                target=self._rx_loop, name="RoverCClientRx", daemon=True
            )
            self._rx_thread.start()

    def send_heartbeat(self) -> None:
        """Send the 1-byte liveness heartbeat. Must be called periodically,
        well inside the firmware failsafe window, or the motors are zeroed."""
        self._sock.sendto(HEARTBEAT_MAGIC, self._addr)

    def send_motion(self, vx: float, vy: float, wz: float, t: float | None = None) -> None:
        pkt = {"t": t if t is not None else time.time(), "vx": vx, "vy": vy, "wz": wz}
        self._sock.sendto(json.dumps(pkt).encode("utf-8"), self._addr)

    def send_config_dict(self, cfg: dict, repeat: int = 3) -> None:
        """Generic config push -- whatever keys are in `cfg` get applied
        firmware-side. Used by `coefs.push_to_firmware` to set max_motor /
        kick_dur_ms / brake_dur_ms / tel."""
        pkt = json.dumps({"cfg": cfg}).encode("utf-8")
        for _ in range(repeat):
            self._sock.sendto(pkt, self._addr)

    def send_poly_chunk(self, buf: bytes) -> None:
        """One 0xC0 polynomial chunk per (wheel, dir), built by
        `coefs.chunk_bytes` (currently 56 bytes)."""
        self._sock.sendto(buf, self._addr)

    def send_stop(self, repeat: int = 3, delay_s: float = 0.02) -> None:
        pkt = json.dumps({"t": time.time(), "vx": 0.0, "vy": 0.0, "wz": 0.0}).encode("utf-8")
        for _ in range(repeat):
            self._sock.sendto(pkt, self._addr)
            time.sleep(delay_s)

    def close(self) -> None:
        self._stop.set()
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=1.0)
        self._sock.close()

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                continue

            # Binary telemetry: cheap dispatch on the magic byte.
            if data[0] == 0xD2 and self._on_telemetry is not None:
                try:
                    self._on_telemetry(data)
                except Exception:
                    # Don't kill the rx thread on a bad telemetry callback.
                    pass
                continue

            # JSON path: camera state push (the only other StickC->PC traffic).
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            cam = payload.get("cam") if isinstance(payload, dict) else None
            if not isinstance(cam, dict) or self._registry is None:
                continue
            for role, entry in cam.items():
                if entry is None:
                    self._registry.clear(role)
                    continue
                if not isinstance(entry, dict):
                    continue
                ip = entry.get("ip")
                p = entry.get("port")
                ok = entry.get("ok", False)
                vbat = entry.get("vbat_mv")
                vbat_mv = int(vbat) if isinstance(vbat, int) else None
                if isinstance(ip, str) and isinstance(p, int):
                    self._registry.update(role, ip, p, bool(ok), vbat_mv=vbat_mv)
