#!/usr/bin/env python3
"""Listen for camera_node UDP broadcast self-announcements.

Usage:
    uv run --no-project python3 scripts/listen_announce.py [--port 4211]

Prints one line per packet with sender IP, elapsed time since the previous
packet from the same sender, and the raw JSON payload.
"""
import argparse
import json
import socket
import time
from pathlib import Path


DEFAULT_PORT = 4211


def load_default_port() -> int:
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    if not config_path.is_file():
        return DEFAULT_PORT
    try:
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        return int(cfg.get("camera", {}).get("announce_port", DEFAULT_PORT))
    except (OSError, ValueError, KeyError):
        return DEFAULT_PORT


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        type=int,
        default=load_default_port(),
        help="UDP port to bind (defaults to config.json camera.announce_port)",
    )
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", args.port))
    print(f"listening on UDP *:{args.port}")

    last_seen: dict[str, float] = {}
    try:
        while True:
            data, addr = sock.recvfrom(2048)
            now = time.monotonic()
            sender = addr[0]
            dt = now - last_seen.get(sender, now)
            last_seen[sender] = now
            try:
                payload = data.decode("utf-8")
            except UnicodeDecodeError:
                payload = repr(data)
            print(f"[{sender}] dt={dt*1000:7.1f}ms  {payload}")
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
