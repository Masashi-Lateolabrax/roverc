"""Polynomial motor-correction coefficients for the RoverC firmware.

Mirrors the firmware-side per-(wheel, dir) coefficients (see
`roverc_server.ino`). Per `(wheel ∈ {FL, FR, RL, RR}, dir ∈ {fwd, bwd})`:

    p_kick(t)    = s · f_k(t),     f_k(0) = 0,        f_k(T_k) = k_steady
    p_steady     = k_steady · s
    p_brake(t)   = s_pre · f_b(t), f_b(0) = k_steady, f_b(T_b) = 0

where `s` is the per-wheel mecanum-mixed normalised command, `s_pre` is
the snapshot at STEADY → BRAKE entry (current `s = 0` during BRAKE so
the brake polynomial uses the snapshot), and `t` is phase-relative time
in seconds.

`f_k` and `f_b` are univariate polynomials in `t` of degree
`poly_order` (default 3, configurable up to firmware cap 5). The
boundary conditions enforce continuity at the KICK→STEADY and
STEADY→BRAKE transitions and a clean stop at BRAKE→IDLE.

The on-disk JSON stores monomial coefficients `c[0..poly_order]` for
human readability; constraint compliance is the responsibility of
`vector_to_coefs` (CMA-ES bridge) and `make_identity`.

Per `(wheel, dir)` free-parameter count:
    1 (k_steady) + (N − 1) (f_k Bernstein interior) + (N − 1) (f_b interior)
    = 2N − 1
For N=3: 5 per cell, 40 total over 4 wheels × 2 dirs.

Non-negativity (`k_steady ≥ 0`, `f_k ≥ 0`, `f_b ≥ 0` ⇒ direction
preserved) is enforced via `b = x²` mapping on the CMA-ES vector. The
Bernstein basis is the intermediate representation: non-negative
control points are a sufficient condition for the polynomial to be
non-negative on `[0, T]`, since the curve is a convex combination of
those points. Conversion to monomial form happens before binary push.
"""
from __future__ import annotations

import json
import math
import struct
import time
from dataclasses import dataclass, field
from math import comb
from pathlib import Path
from typing import Callable

DIRS = ("fwd", "bwd")
WHEEL_NAMES = ("FL", "FR", "RL", "RR")
N_WHEELS = 4
DEFAULT_POLY_ORDER = 3
POLY_MAX_ORDER = 5                # firmware compile-time cap
POLY_NCOEFS = POLY_MAX_ORDER + 1  # wire format always carries 6 floats per poly

# Binary chunk wire format (must match firmware constants in roverc_server.ino):
#   [0]      magic 0xC0
#   [1]      wheel  (0..3, FL FR RL RR)
#   [2]      dir    (0=fwd, 1=bwd)
#   [3]      reserved (=0)
#   [4..7]   k_steady               (float LE)
#   [8..31]  kick c[0..POLY_MAX_ORDER]   (POLY_NCOEFS floats LE, monomial)
#   [32..55] brake c[0..POLY_MAX_ORDER]  (POLY_NCOEFS floats LE, monomial)
# Total 56 bytes. 8 chunks (4 wheels × 2 dirs) = full coef table.
POLY_CHUNK_MAGIC = 0xC0
POLY_CHUNK_BYTES = 4 + 4 + POLY_NCOEFS * 4 + POLY_NCOEFS * 4   # = 56

CellKey = tuple[int, str]   # (wheel 0..3, dir)


def _zero_poly() -> list[float]:
    return [0.0] * POLY_NCOEFS


@dataclass
class PerDirCoefs:
    """Coefficients for one wheel × one direction.

    `kick` and `brake` are full monomial polynomials of length
    `POLY_NCOEFS` (slots beyond `poly_order` are zero-padded). Boundary
    conditions are not enforced by this class -- callers go through
    `vector_to_coefs` or `make_identity` to get a constraint-respecting
    set."""
    k_steady: float = 1.0
    kick: list[float] = field(default_factory=_zero_poly)
    brake: list[float] = field(default_factory=_zero_poly)


@dataclass
class CoefSet:
    """Full coefficient table plus the scalar phase-duration / max-motor
    settings that the firmware also needs."""
    poly_order: int = DEFAULT_POLY_ORDER
    max_motor: int = 60
    kick_dur_ms: int = 100
    brake_dur_ms: int = 100
    cells: dict[CellKey, PerDirCoefs] = field(default_factory=dict)

    @staticmethod
    def cell_keys() -> list[CellKey]:
        """Canonical cell ordering used for vector packing and binary push."""
        return [(w, d) for w in range(N_WHEELS) for d in DIRS]

    def normalize(self) -> None:
        """Ensure all 8 cells exist; missing ones become identity defaults."""
        for key in self.cell_keys():
            if key not in self.cells:
                self.cells[key] = PerDirCoefs()


# ---------------------------------------------------------------------------
# Bernstein <-> monomial basis
# ---------------------------------------------------------------------------

def _bernstein_to_monomial_unit(bs: list[float]) -> list[float]:
    """Convert Bernstein control points `bs[0..N]` on `u ∈ [0, 1]` to
    monomial basis `c[0..N]`, padded to length POLY_NCOEFS.

    `B(u) = Σ_i C(N, i) · b_i · u^i · (1-u)^(N-i)`
         `= Σ_i Σ_j C(N, i) · C(N-i, j) · (-1)^j · b_i · u^(i+j)`
    so `c[k] = Σ_{i ≤ k} C(N, i) · C(N-i, k-i) · (-1)^(k-i) · b_i`.
    """
    n = len(bs) - 1
    c = [0.0] * (n + 1)
    for i in range(n + 1):
        for j in range(n - i + 1):
            sign = -1.0 if (j & 1) else 1.0
            c[i + j] += comb(n, i) * comb(n - i, j) * sign * bs[i]
    while len(c) < POLY_NCOEFS:
        c.append(0.0)
    return c


def _monomial_unit_to_bernstein(c_unit: list[float], n: int) -> list[float]:
    """Inverse of `_bernstein_to_monomial_unit` for degree `n`. Solves
    the upper-triangular linear system `M · b = c` where `M[k][i]`
    matches the forward map. Returns `b[0..n]`."""
    import numpy as np
    M = np.zeros((n + 1, n + 1), dtype=np.float64)
    for k in range(n + 1):
        for i in range(k + 1):
            sign = 1.0 if (k - i) % 2 == 0 else -1.0
            M[k][i] = comb(n, i) * comb(n - i, k - i) * sign
    c_vec = np.array(c_unit[:n + 1], dtype=np.float64)
    b_vec = np.linalg.solve(M, c_vec)
    return b_vec.tolist()


def _rescale_monomial(c_unit: list[float], T_sec: float) -> list[float]:
    """Rescale monomial coefs from `u ∈ [0, 1]` to `t ∈ [0, T_sec]`. If
    `B(u) = Σ c'[k] u^k`, substituting `u = t/T` yields
    `f(t) = Σ (c'[k] / T^k) · t^k`. `T_sec ≤ 0` (degenerate phase
    duration) collapses to a constant term only."""
    if T_sec <= 0.0:
        return [c_unit[0]] + [0.0] * (POLY_NCOEFS - 1)
    out = []
    Tk = 1.0
    for k in range(POLY_NCOEFS):
        out.append(c_unit[k] / Tk if k < len(c_unit) else 0.0)
        Tk *= T_sec
    return out


def _undo_rescale_monomial(c: list[float], T_sec: float, n: int) -> list[float]:
    """Inverse of `_rescale_monomial`: convert `c[k]` (in `t ∈ [0, T]`)
    back to unit-time monomial `c'[k] = c[k] · T^k` for `k = 0..n`."""
    if T_sec <= 0.0:
        return [c[0]] + [0.0] * n
    out = []
    Tk = 1.0
    for k in range(n + 1):
        out.append(c[k] * Tk if k < len(c) else 0.0)
        Tk *= T_sec
    return out


# ---------------------------------------------------------------------------
# Free-parameter <-> CoefSet bridge for CMA-ES
# ---------------------------------------------------------------------------

def free_params_per_cell(poly_order: int) -> int:
    """Free parameter count per `(wheel, dir)` for a given polynomial
    degree. Layout in the flat vector:
        [k_steady, kick_b_1..kick_b_{N-1}, brake_b_1..brake_b_{N-1}]
    """
    return 1 + 2 * (poly_order - 1)


def total_free_params(poly_order: int) -> int:
    return N_WHEELS * len(DIRS) * free_params_per_cell(poly_order)


def make_identity(
    poly_order: int = DEFAULT_POLY_ORDER,
    max_motor: int = 60,
    kick_dur_ms: int = 100,
    brake_dur_ms: int = 100,
) -> CoefSet:
    """Identity baseline: `k_steady = 1`, `f_k(t) = t / T_k` (linear ramp
    up satisfying boundary conditions), `f_b(t) = 1 - t / T_b` (linear
    decay). Constraint-respecting and non-negative on the phase
    intervals. Independent of `poly_order` since linear is degree 1 and
    the higher-order coefficients are zero-padded."""
    cs = CoefSet(
        poly_order=poly_order,
        max_motor=max_motor,
        kick_dur_ms=kick_dur_ms,
        brake_dur_ms=brake_dur_ms,
    )
    Tk_sec = kick_dur_ms / 1000.0
    Tb_sec = brake_dur_ms / 1000.0
    kick_mono = _zero_poly()
    brake_mono = _zero_poly()
    if Tk_sec > 0.0:
        kick_mono[1] = 1.0 / Tk_sec   # f_k(t) = t / T_k
    if Tb_sec > 0.0:
        brake_mono[0] = 1.0
        brake_mono[1] = -1.0 / Tb_sec  # f_b(t) = 1 - t / T_b
    else:
        brake_mono[0] = 1.0
    for w in range(N_WHEELS):
        for d in DIRS:
            cs.cells[(w, d)] = PerDirCoefs(
                k_steady=1.0,
                kick=list(kick_mono),
                brake=list(brake_mono),
            )
    return cs


def vector_to_coefs(v, template: CoefSet) -> CoefSet:
    """Build a CoefSet from a CMA-ES free-parameter vector.

    `v` has shape `(8 · (2N − 1),)` with `N = template.poly_order`.
    Components are squared (x² mapping) for non-negativity, treated as
    Bernstein control points (with the boundary-fixed endpoints
    inserted), then converted to monomial form scaled to the
    appropriate phase duration. Inherits scalar fields from `template`.
    """
    import numpy as np
    N = template.poly_order
    fpc = free_params_per_cell(N)
    expected = total_free_params(N)
    if len(v) != expected:
        raise ValueError(f"vector length {len(v)} != expected {expected}")
    v_arr = np.asarray(v, dtype=np.float64)
    v_sq = v_arr * v_arr
    cs = CoefSet(
        poly_order=N,
        max_motor=template.max_motor,
        kick_dur_ms=template.kick_dur_ms,
        brake_dur_ms=template.brake_dur_ms,
    )
    Tk_sec = template.kick_dur_ms / 1000.0
    Tb_sec = template.brake_dur_ms / 1000.0
    n_int = N - 1
    for ci, key in enumerate(cs.cell_keys()):
        offset = ci * fpc
        k_steady = float(v_sq[offset])
        kick_int = [float(v_sq[offset + 1 + i]) for i in range(n_int)]
        brake_int = [float(v_sq[offset + 1 + n_int + i]) for i in range(n_int)]
        # Bernstein endpoints fixed by boundary conditions:
        kick_bs = [0.0] + kick_int + [k_steady]
        brake_bs = [k_steady] + brake_int + [0.0]
        kick_unit = _bernstein_to_monomial_unit(kick_bs)
        brake_unit = _bernstein_to_monomial_unit(brake_bs)
        cs.cells[key] = PerDirCoefs(
            k_steady=k_steady,
            kick=_rescale_monomial(kick_unit, Tk_sec),
            brake=_rescale_monomial(brake_unit, Tb_sec),
        )
    return cs


def coefs_to_vector(cs: CoefSet):
    """Inverse of `vector_to_coefs`: extract free parameters from a
    CoefSet. Inverts the rescale and Bernstein conversion, takes the
    componentwise sqrt (taking max with 0 first to guard against
    floating-point negatives in stored coefficients)."""
    import numpy as np
    N = cs.poly_order
    fpc = free_params_per_cell(N)
    out = np.zeros(total_free_params(N), dtype=np.float64)
    Tk_sec = cs.kick_dur_ms / 1000.0
    Tb_sec = cs.brake_dur_ms / 1000.0
    n_int = N - 1
    for ci, key in enumerate(cs.cell_keys()):
        cell = cs.cells.get(key, PerDirCoefs())
        offset = ci * fpc
        kick_unit = _undo_rescale_monomial(cell.kick, Tk_sec, N)
        brake_unit = _undo_rescale_monomial(cell.brake, Tb_sec, N)
        kick_bs = _monomial_unit_to_bernstein(kick_unit, N)
        brake_bs = _monomial_unit_to_bernstein(brake_unit, N)
        out[offset] = math.sqrt(max(0.0, cell.k_steady))
        for i in range(n_int):
            out[offset + 1 + i] = math.sqrt(max(0.0, kick_bs[1 + i]))
            out[offset + 1 + n_int + i] = math.sqrt(max(0.0, brake_bs[1 + i]))
    return out


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------

JSON_VERSION = 2  # bumped from v1 (bivariate s,t with f and g) to v2 (univariate t)


def load_json(path: Path | str) -> CoefSet:
    p = Path(path)
    obj = json.loads(p.read_text(encoding="utf-8"))
    version = int(obj.get("version", 0))
    if version != JSON_VERSION:
        raise ValueError(
            f"{p}: unsupported coef JSON version {version} "
            f"(expected {JSON_VERSION}). The motor model was reworked "
            "(univariate `f_k(t)` / `f_b(t)` with boundary conditions); "
            "regenerate identity via scripts/make_identity_coefs.py and "
            "recalibrate."
        )
    cs = CoefSet(
        poly_order=int(obj.get("poly_order", DEFAULT_POLY_ORDER)),
        max_motor=int(obj.get("max_motor", 60)),
        kick_dur_ms=int(obj.get("kick_dur_ms", 100)),
        brake_dur_ms=int(obj.get("brake_dur_ms", 100)),
    )
    for entry in obj.get("cells", []):
        w = int(entry["wheel"])
        d = str(entry["dir"])
        if d not in DIRS or not (0 <= w < N_WHEELS):
            continue
        k_steady = float(entry.get("k_steady", 1.0))
        kick = [float(x) for x in entry.get("kick", _zero_poly())]
        brake = [float(x) for x in entry.get("brake", _zero_poly())]
        # Pad to wire format length so downstream code can index uniformly.
        kick = (kick + [0.0] * POLY_NCOEFS)[:POLY_NCOEFS]
        brake = (brake + [0.0] * POLY_NCOEFS)[:POLY_NCOEFS]
        cs.cells[(w, d)] = PerDirCoefs(
            k_steady=k_steady,
            kick=kick,
            brake=brake,
        )
    cs.normalize()
    return cs


def save_json(cs: CoefSet, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cells = []
    for key in cs.cell_keys():
        cell = cs.cells.get(key, PerDirCoefs())
        w, d = key
        cells.append({
            "wheel": w,
            "dir": d,
            "k_steady": cell.k_steady,
            "kick": list(cell.kick),
            "brake": list(cell.brake),
        })
    obj = {
        "version": JSON_VERSION,
        "poly_order": cs.poly_order,
        "max_motor": cs.max_motor,
        "kick_dur_ms": cs.kick_dur_ms,
        "brake_dur_ms": cs.brake_dur_ms,
        "cells": cells,
    }
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Binary push to firmware
# ---------------------------------------------------------------------------

def chunk_bytes(wheel: int, dir_idx: int, cell: PerDirCoefs) -> bytes:
    """Pack one (wheel, dir) cell into the 56-byte 0xC0 wire format."""
    fmt = f"<BBBBf{POLY_NCOEFS}f{POLY_NCOEFS}f"
    buf = struct.pack(
        fmt,
        POLY_CHUNK_MAGIC, wheel, dir_idx, 0,
        float(cell.k_steady),
        *cell.kick,
        *cell.brake,
    )
    assert len(buf) == POLY_CHUNK_BYTES, (len(buf), POLY_CHUNK_BYTES)
    return buf


def push_to_firmware(
    cs: CoefSet,
    send_chunk: Callable[[bytes], None],
    send_cfg_dict: Callable[[dict], None],
    repeat: int = 2,
    inter_chunk_ms: float = 8.0,
) -> int:
    """Push scalar cfg + 8 binary chunks. Inter-chunk delay matches the
    ESP32 LWIP UDP rx queue depth (~6-8 packets); duplication is the
    same durability pattern that `RoverCClient.send_config(repeat=3)`
    uses for the JSON cfg envelope. Returns total chunk transmissions
    (8 × repeat). Callbacks let us avoid importing roverc here (no
    circular dependency)."""
    send_cfg_dict({
        "mx": int(cs.max_motor),
        "kdur": int(cs.kick_dur_ms),
        "bdur": int(cs.brake_dur_ms),
    })
    time.sleep(0.02)
    sent = 0
    for w in range(N_WHEELS):
        for d_idx, d in enumerate(DIRS):
            cell = cs.cells.get((w, d), PerDirCoefs())
            buf = chunk_bytes(w, d_idx, cell)
            for _ in range(repeat):
                send_chunk(buf)
                sent += 1
                time.sleep(inter_chunk_ms / 1000.0)
    return sent
