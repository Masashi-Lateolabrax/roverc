#!/usr/bin/env python3
"""Keyboard teleop for RoverC via the StickC Plus2 UDP server.

Reads server address and rate from config.json. Captures key presses on the
controlling terminal and sends a small JSON packet per tick. Stdlib only.

Usage:
    python3 teleop.py --config ../../config.json
"""
from __future__ import annotations

import argparse
import json
import os
import select
import signal
import socket
import sys
import termios
import time
import tty
from pathlib import Path

KEY_VX = 0.6
KEY_VY = 0.6
KEY_WZ = 0.6
KEY_TIMEOUT_S = 0.15  # release detection: no repeat within this window -> released


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class RawTerminal:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.saved = None

    def __enter__(self) -> "RawTerminal":
        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc) -> None:
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)


def drain_keys(fd: int) -> str:
    chars = []
    while True:
        r, _, _ = select.select([fd], [], [], 0)
        if not r:
            break
        ch = os.read(fd, 1).decode("latin-1", errors="ignore")
        if not ch:
            break
        chars.append(ch)
    return "".join(chars)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[2] / "config.json"),
        help="Path to config.json (default: repo-root/config.json)",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    host = cfg["server"]["host"]
    port = int(cfg["server"]["port"])
    rate_hz = int(cfg["control"]["rate_hz"])
    period = 1.0 / rate_hz

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (host, port)

    print(f"target  : udp {host}:{port}")
    print(f"rate    : {rate_hz} Hz")
    print("keys    : w/s = +/- vx, a/d = -/+ vy, q/e = +/- wz")
    print("        : space = stop, Ctrl-C / Esc = quit")
    print("press a key to start...")

    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        print("teleop.py requires a TTY for keyboard input", file=sys.stderr)
        return 2

    last_seen: dict[str, float] = {}
    stop = False

    def on_sigint(_sig, _frm):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_sigint)

    with RawTerminal(fd):
        next_t = time.monotonic()
        while not stop:
            now = time.monotonic()
            if now < next_t:
                time.sleep(min(period, next_t - now))
                continue
            next_t += period

            chars = drain_keys(fd)
            for ch in chars:
                if ch == "\x1b":  # Esc
                    stop = True
                elif ch == " ":
                    last_seen.clear()
                elif ch in "wsadqe":
                    last_seen[ch] = now

            for k in list(last_seen):
                if now - last_seen[k] > KEY_TIMEOUT_S:
                    del last_seen[k]

            vx = (KEY_VX if "w" in last_seen else 0.0) - (
                KEY_VX if "s" in last_seen else 0.0
            )
            vy = (KEY_VY if "d" in last_seen else 0.0) - (
                KEY_VY if "a" in last_seen else 0.0
            )
            wz = (KEY_WZ if "q" in last_seen else 0.0) - (
                KEY_WZ if "e" in last_seen else 0.0
            )

            packet = {"t": time.time(), "vx": vx, "vy": vy, "wz": wz}
            sock.sendto(json.dumps(packet).encode("utf-8"), addr)

            sys.stdout.write(
                f"\rvx={vx:+.2f} vy={vy:+.2f} wz={wz:+.2f}  "
            )
            sys.stdout.flush()

    stop_packet = json.dumps({"t": time.time(), "vx": 0.0, "vy": 0.0, "wz": 0.0}).encode(
        "utf-8"
    )
    for _ in range(3):
        sock.sendto(stop_packet, addr)
        time.sleep(0.02)

    sys.stdout.write("\nstopped\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
