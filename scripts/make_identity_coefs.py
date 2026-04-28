#!/usr/bin/env python3
"""Emit identity polynomial coefficients to a JSON file.

The "identity" set reproduces the firmware's natural baseline:
  k_steady = 1   (STEADY: p = s)
  f_k(t)   = t / T_k        (linear ramp 0 → 1, satisfies boundary conditions)
  f_b(t)   = 1 − t / T_b    (linear decay, satisfies boundary conditions)

Usage:
    uv run python scripts/make_identity_coefs.py coefs/identity.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the python_client modules importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "python_client"))

from coefs import DEFAULT_POLY_ORDER, POLY_MAX_ORDER, make_identity, save_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path)
    ap.add_argument("--poly-order", type=int, default=DEFAULT_POLY_ORDER,
                    help=f"Polynomial degree N (default {DEFAULT_POLY_ORDER}, "
                         f"max {POLY_MAX_ORDER}).")
    ap.add_argument("--max-motor", type=int, default=60)
    ap.add_argument("--kick-dur-ms", type=int, default=100)
    ap.add_argument("--brake-dur-ms", type=int, default=100)
    args = ap.parse_args()

    if not (1 <= args.poly_order <= POLY_MAX_ORDER):
        ap.error(f"--poly-order must be in [1, {POLY_MAX_ORDER}]")

    cs = make_identity(
        poly_order=args.poly_order,
        max_motor=args.max_motor,
        kick_dur_ms=args.kick_dur_ms,
        brake_dur_ms=args.brake_dur_ms,
    )
    save_json(cs, args.path)
    print(f"wrote {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
