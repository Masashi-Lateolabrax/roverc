#!/usr/bin/env python3
"""Show the front camera with OpenCV. Press Esc or q to quit.

Usage:
    uv run src/camera_example.py --host 192.168.1.123   # StickC Plus2 IP
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent / "crover_mod"))

from rover import Rover  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", required=True, help="StickC Plus2 IP")
    args = ap.parse_args()

    rover = Rover(args.host)
    print("waiting for camera... (Esc or q to quit)")
    try:
        while True:
            img = rover.get_camera()
            if img is not None:
                cv2.imshow("roverc camera", img)
            if cv2.waitKey(10) & 0xFF in (27, ord("q")):
                break
    except KeyboardInterrupt:
        pass
    finally:
        rover.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
