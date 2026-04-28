"""UDP client for the RoverC StickC Plus2 server.

Wire protocol on a single UDP socket (PC <-> StickC):

PC -> StickC
- motion packet: {"t": ..., "vx": ..., "vy": ..., "wz": ...}
- config packet: {"cfg": {"mx": ..., "kdur": ..., "tf": [...], "tb": [...], "kf": [...], "kb": [...]}}

StickC -> PC (push, ~1 Hz, only after the StickC has received at least one
packet from this client so it knows where to reply)
- camera state: {"cam": {"left": {"ip": "...", "port": 80, "ok": true} | null,
                        "right": {...} | null}}

The kick state machine runs on the StickC's 50 Hz local loop, so the PC only
sends motion at the configured rate and pushes config on demand. If the
caller passes a `CameraRegistry`, the client spawns a receive thread that
parses incoming camera-state packets and updates the registry.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Sequence

from camera import CameraRegistry


class RoverCClient:
    def __init__(
        self,
        host: str,
        port: int,
        camera_registry: CameraRegistry | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._addr = (host, port)
        self._registry = camera_registry
        self._stop = threading.Event()
        self._rx_thread: threading.Thread | None = None
        if camera_registry is not None:
            self._sock.settimeout(0.5)
            self._rx_thread = threading.Thread(
                target=self._rx_loop, name="RoverCClientRx", daemon=True
            )
            self._rx_thread.start()

    def send_motion(self, vx: float, vy: float, wz: float, t: float | None = None) -> None:
        pkt = {"t": t if t is not None else time.time(), "vx": vx, "vy": vy, "wz": wz}
        self._sock.sendto(json.dumps(pkt).encode("utf-8"), self._addr)

    def send_config(
        self,
        max_motor: int,
        kick_dur_ms: int,
        trim_fwd: Sequence[float],
        trim_bwd: Sequence[float],
        kick_fwd: Sequence[float],
        kick_bwd: Sequence[float],
        repeat: int = 3,
    ) -> None:
        cfg = {
            "mx": int(max_motor),
            "kdur": int(kick_dur_ms),
            "tf": list(trim_fwd),
            "tb": list(trim_bwd),
            "kf": list(kick_fwd),
            "kb": list(kick_bwd),
        }
        pkt = json.dumps({"cfg": cfg}).encode("utf-8")
        for _ in range(repeat):
            self._sock.sendto(pkt, self._addr)

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
                if isinstance(ip, str) and isinstance(p, int):
                    self._registry.update(role, ip, p, bool(ok))
