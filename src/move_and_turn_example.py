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

from rover import Rover  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", required=True, help="StickC Plus2 IP")
    args = ap.parse_args()

    rover = Rover(args.host)
    rover.push_motor_config()
    print("forward 3 s / spin 2 s, repeating (Ctrl-C to stop)")
    try:
        while True:
            rover.move(vx=0.5)  # forward
            time.sleep(3)
            rover.move(wz=0.5)  # spin in place (wz > 0 = CCW)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        rover.stop()
        rover.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
