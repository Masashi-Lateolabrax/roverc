#!/usr/bin/env python3
"""Emit identity polynomial coefficients to a JSON file.

The "identity" set reproduces the firmware's natural baseline:
  k_steady = 1   (STEADY: p = s)
  q_k = r_k = √(1)/T_k  constant   →  f_k(t) = t / T_k       (linear ramp)
  q_b = r_b = √(1)/T_b  constant   →  f_b(t) = 1 − t / T_b   (linear decay)

Usage:
    uv run python scripts/make_identity_coefs.py coefs/identity.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the crover_mod modules importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "crover_mod"))

from coefs import DEFAULT_M_ORDER, M_MAX_ORDER, make_identity, save_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path)
    ap.add_argument("--m-order", type=int, default=DEFAULT_M_ORDER,
                    help=f"Half the f-polynomial degree (default "
                         f"{DEFAULT_M_ORDER}, max {M_MAX_ORDER}). Final f "
                         f"polynomial degree is 2·m_order.")
    ap.add_argument("--max-motor", type=int, default=60)
    ap.add_argument("--kick-dur-ms", type=int, default=100)
    ap.add_argument("--brake-dur-ms", type=int, default=100)
    args = ap.parse_args()

    if not (1 <= args.m_order <= M_MAX_ORDER):
        ap.error(f"--m-order must be in [1, {M_MAX_ORDER}]")

    cs = make_identity(
        m_order=args.m_order,
        max_motor=args.max_motor,
        kick_dur_ms=args.kick_dur_ms,
        brake_dur_ms=args.brake_dur_ms,
    )
    save_json(cs, args.path)
    print(f"wrote {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
