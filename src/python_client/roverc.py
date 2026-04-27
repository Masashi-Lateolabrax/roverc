"""UDP client for the RoverC StickC Plus2 server.

The server distinguishes packets by the presence of a "cfg" key:
- motion packet: {"t": ..., "vx": ..., "vy": ..., "wz": ...}
- config packet: {"cfg": {"mx": ..., "kdur": ..., "tf": [...], "tb": [...], "kf": [...], "kb": [...]}}

The server applies the kick state machine on its own 50 Hz control loop, so the
client only sends motion at the configured rate and pushes config on demand.
"""
from __future__ import annotations

import json
import socket
import time
from typing import Sequence


class RoverCClient:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._addr = (host, port)

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
        self._sock.close()
