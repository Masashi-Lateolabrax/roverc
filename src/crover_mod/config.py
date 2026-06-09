"""Typed access to config.json.

`Config` loads the project config file once from an explicit path and exposes
the sections the Python client needs (server port, camera announce port, camera
framesize / quality, and the raw dict for the motor coefficient table). Examples
build one at startup and hand it to `Rover`, so the config path lives in the
example, not buried inside the library.

    config = Config("config.json")
    rover = Rover(host, config, max_throttle=0.5)
"""
from __future__ import annotations

import json
from pathlib import Path

from camera import framesize_from_name


class Config:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with open(self.path) as f:
            self.raw = json.load(f)

    @property
    def port(self) -> int:
        """UDP control port the StickC server listens on (server.port)."""
        return int(self.raw.get("server", {}).get("port", 4210))

    @property
    def announce_port(self) -> int:
        """UDP port the camera broadcasts its announce on (camera.announce_port)."""
        return int(self.raw.get("camera", {}).get("announce_port", 4211))

    @property
    def camera_framesize(self) -> int | None:
        """camera.framesize name (e.g. "VGA") resolved to its wire integer, or
        None to leave the firmware default untouched."""
        fs = self.raw.get("camera", {}).get("framesize")
        return framesize_from_name(fs) if isinstance(fs, str) else None

    @property
    def camera_quality(self) -> int | None:
        """camera.quality (JPEG quality 0-63, lower = better), or None."""
        q = self.raw.get("camera", {}).get("quality")
        return int(q) if isinstance(q, (int, float)) else None
