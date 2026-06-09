#!/usr/bin/env python3
"""Drive forward 3 s, spin in place 2 s, repeat. Ctrl-C to stop.

Usage:
    uv run src/move_and_turn_example.py --host 192.168.1.123   # StickC Plus2 IP
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "crover_mod"))

from config import Config  # noqa: E402
from rover import Rover  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", required=True, help="StickC Plus2 IP")
    args = ap.parse_args()

    config = Config("config.json")
    rover = Rover(args.host, config)
    rover.push_motor_config()
    print("forward 3 s / spin 2 s, repeating (Ctrl-C to stop)")
    try:
        while True:
            rover.move((0.5, 0.0), turn=0.0)  # forward at half output, no rotation
            time.sleep(3)
            rover.move((0.0, 0.0), turn=0.5)  # spin in place (> 0 = CCW)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        rover.stop()
        rover.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
