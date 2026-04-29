"""Polynomial motor-correction coefficients for the RoverC firmware.

Mirrors the firmware-side per-(wheel, dir) coefficients (see
`roverc_server.ino`). Per `(wheel ∈ {FL, FR, RL, RR}, dir ∈ {fwd, bwd})`:

    p_kick(t)    = s · f_k(t),     f_k(0) = 0,        f_k(T_k) = k_steady
    p_steady     = k_steady · s
    p_brake(t)   = s_pre · f_b(t), f_b(0) = k_steady, f_b(T_b) = 0

where `s` is the per-wheel mecanum-mixed normalised command, `s_pre` is
the snapshot at STEADY → BRAKE entry, and `t` is phase-relative time.

Non-negativity is structural via the **even-degree Lukács theorem**
plus BRAKE input inversion (s = T_b − t):

    f_k(t) = t² · q_k(t)² + t(T_k − t) · r_k(t)²            (KICK)
    f_b(t) = (T_b−t)² · q_b(T_b−t)² + t(T_b−t) · r_b(T_b−t)²  (BRAKE)

Derivation: the [0, T] even-degree Lukács form of a non-negative
polynomial is `q² + t(T−t)·r²`. Imposing `f(0) = 0` (KICK) forces
`q(0) = 0`, so `q = t · q̃`, giving the t² factor. The same form
applies to BRAKE after substituting s = T_b − t (the input-inversion
trick): the BRAKE boundary `f_b(T_b) = 0` becomes `f̃_b(0) = 0` on
the inverted variable, structurally identical to KICK.

`q_k`, `r_k` are polynomials in `t` (or `u = t/T_k`, equivalent up to
rescale). `q_b`, `r_b` are polynomials in `s = T_b − t` (or `u = s/T_b`).
All four are stored canonically in unit time `u ∈ [0, 1]`, length
`m_order`. The `f_k`/`f_b` monomial in `t` is computed lazily for the
wire format.

The single linear boundary at the non-zero endpoint determines one
scalar each:

    Q_k(1) = √(k_steady) / T_k        (forces f_k(T_k) = k_steady)
    Q_b(1) = √(k_steady) / T_b        (forces f_b(0)   = k_steady)

CMA-ES free-parameter layout per `(wheel, dir)`:
    [x_k_steady,
     α_k_0..α_k_{m-2},  β_k_0..β_k_{m-1},
     α_b_0..α_b_{m-2},  β_b_0..β_b_{m-1}]

  * `k_steady = x_k_steady²` (scalar gain non-negative)
  * α_i: dimensionless (Q_unit_coef[i] = α_i · target_q, Σα = 1)
  * β_i: dimensionless (R_unit_coef[i] = β_i · target_r)
  * `target_q = target_r = √(k_steady) / T`
  * α_last is determined by Σα = 1 (sum constraint enforces boundary)
  * β has no boundary constraint, all m coefficients free
  * Identity vector: `[1, 1, 0, ..., 0, 1, 0, ..., 0, 1, 0, ..., 0, 1, 0, ..., 0]`
    yields the linear ramp `f_k(t) = (k/T_k)·t` and linear decay
    `f_b(t) = (k/T_b)·(T_b − t)`.

Per cell `1 + 2·(2m − 1) = 4m − 1` free params. f polynomial has degree
`2·m_order`. With m=2 (default), f is degree 4; cell free = 7; total
across 8 cells = 56. Firmware's `POLY_MAX_ORDER = 5` allows m up to 2.

The on-disk JSON canonical form stores q_k, r_k, q_b, r_b in unit time.
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
DEFAULT_M_ORDER = 2
POLY_MAX_ORDER = 5                # firmware compile-time cap on f(t) degree
POLY_NCOEFS = POLY_MAX_ORDER + 1  # wire format always carries 6 floats per poly
M_MAX_ORDER = POLY_MAX_ORDER // 2  # f degree = 2·m, so m ≤ POLY_MAX_ORDER//2

# Binary chunk wire format (must match firmware constants in roverc_server.ino):
#   [0]      magic 0xC0
#   [1]      wheel  (0..3, FL FR RL RR)
#   [2]      dir    (0=fwd, 1=bwd)
#   [3]      reserved (=0)
#   [4..7]   k_steady               (float LE)
#   [8..31]  kick c[0..POLY_MAX_ORDER]   (POLY_NCOEFS floats LE, monomial in t)
#   [32..55] brake c[0..POLY_MAX_ORDER]  (POLY_NCOEFS floats LE, monomial in t)
# Total 56 bytes. 8 chunks (4 wheels × 2 dirs) = full coef table.
POLY_CHUNK_MAGIC = 0xC0
POLY_CHUNK_BYTES = 4 + 4 + POLY_NCOEFS * 4 + POLY_NCOEFS * 4   # = 56

CellKey = tuple[int, str]


def _const_unit(m_order: int) -> list[float]:
    """Constant-1 polynomial of length `m_order` in unit time."""
    return [1.0] + [0.0] * (m_order - 1)


@dataclass
class PerDirCoefs:
    """Coefficients for one wheel × one direction, canonical even-Lukács
    form. All four polynomials stored in unit time, length `m_order`:

      * `q_k`, `r_k` — polynomials in `u = t / T_k` (KICK side)
      * `q_b`, `r_b` — polynomials in `u = (T_b − t) / T_b` (BRAKE side,
        input-inverted)

    Boundary conditions are not enforced by this class; callers go
    through `vector_to_coefs` or `make_identity` to get a
    constraint-respecting set."""
    k_steady: float = 1.0
    q_k: list[float] = field(default_factory=lambda: [1.0])
    r_k: list[float] = field(default_factory=lambda: [1.0])
    q_b: list[float] = field(default_factory=lambda: [1.0])
    r_b: list[float] = field(default_factory=lambda: [1.0])


@dataclass
class CoefSet:
    """Full coefficient table plus the scalar phase-duration / max-motor
    settings that the firmware also needs."""
    m_order: int = DEFAULT_M_ORDER
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
                self.cells[key] = PerDirCoefs(
                    q_k=_const_unit(self.m_order),
                    r_k=_const_unit(self.m_order),
                    q_b=_const_unit(self.m_order),
                    r_b=_const_unit(self.m_order),
                )


# ---------------------------------------------------------------------------
# Polynomial arithmetic
# ---------------------------------------------------------------------------

def _poly_mul(a: list[float], b: list[float]) -> list[float]:
    """Polynomial product; result length = len(a) + len(b) − 1."""
    out = [0.0] * (len(a) + len(b) - 1)
    for i, ai in enumerate(a):
        for j, bj in enumerate(b):
            out[i + j] += ai * bj
    return out


def _poly_add(a: list[float], b: list[float]) -> list[float]:
    n = max(len(a), len(b))
    return [(a[i] if i < len(a) else 0.0) + (b[i] if i < len(b) else 0.0)
            for i in range(n)]


def _poly_neg(a: list[float]) -> list[float]:
    return [-x for x in a]


def _poly_shift(p: list[float], k: int) -> list[float]:
    """Multiply polynomial by `t^k` (prepend k zeros)."""
    return [0.0] * k + list(p)


def _scale_unit_to_t(coefs_u: list[float], T: float) -> list[float]:
    """Convert a polynomial given in `u = t/T` coefficients to `t`
    coefficients. `c_u[i] · u^i = (c_u[i] / T^i) · t^i`. Returns a list
    of the same length; T ≤ 0 collapses to the constant term only."""
    if T <= 0.0:
        return [coefs_u[0] if coefs_u else 0.0] + [0.0] * (len(coefs_u) - 1)
    out = []
    Ti = 1.0
    for c in coefs_u:
        out.append(c / Ti)
        Ti *= T
    return out


def _substitute_T_minus_t(coefs_s: list[float], T: float) -> list[float]:
    """Given a polynomial `p(s) = Σ c_i · s^i` (in s = T − t), return
    coefficients of `p(T − t)` as a polynomial in `t`. Uses binomial
    expansion: `(T − t)^i = Σ_j C(i,j) · T^(i−j) · (−t)^j`."""
    n = len(coefs_s)
    out = [0.0] * n
    for i, ci in enumerate(coefs_s):
        for j in range(i + 1):
            sign = 1.0 if j % 2 == 0 else -1.0
            out[j] += ci * comb(i, j) * (T ** (i - j)) * sign
    return out


def _pad_to(p: list[float], n: int) -> list[float]:
    return (list(p) + [0.0] * n)[:n]


# ---------------------------------------------------------------------------
# f_k(t) and f_b(t) monomial construction
# ---------------------------------------------------------------------------

def _f_kick_monomial(q_unit: list[float], r_unit: list[float], T_k: float) -> list[float]:
    """Compute `f_k(t) = t² · q(t)² + t(T_k − t) · r(t)²` monomial in
    `t`, where `q`, `r` are given in unit time `u = t / T_k`. Returns
    length `POLY_NCOEFS`, zero-padded."""
    if T_k <= 0.0:
        return [0.0] * POLY_NCOEFS
    q_t = _scale_unit_to_t(q_unit, T_k)
    r_t = _scale_unit_to_t(r_unit, T_k)
    q2 = _poly_mul(q_t, q_t)
    r2 = _poly_mul(r_t, r_t)
    # t² · q²
    term1 = _poly_shift(q2, 2)
    # t(T_k − t) · r² = T_k · t · r² − t² · r²
    term2 = _poly_add(_poly_shift([T_k * c for c in r2], 1),
                      _poly_neg(_poly_shift(r2, 2)))
    return _pad_to(_poly_add(term1, term2), POLY_NCOEFS)


def _f_brake_monomial(q_unit: list[float], r_unit: list[float], T_b: float) -> list[float]:
    """Compute `f_b(t) = (T_b − t)² · q(T_b − t)² + t(T_b − t) · r(T_b − t)²`
    monomial in `t`. Computes the structurally identical form in
    `s = T_b − t` (same as kick), then substitutes back. `q`, `r` are
    given in unit `u = s / T_b`."""
    if T_b <= 0.0:
        return [0.0] * POLY_NCOEFS
    f_s = _f_kick_monomial(q_unit, r_unit, T_b)   # in s-space
    return _pad_to(_substitute_T_minus_t(f_s, T_b), POLY_NCOEFS)


# ---------------------------------------------------------------------------
# Free-parameter <-> CoefSet bridge for CMA-ES
# ---------------------------------------------------------------------------

def free_params_per_cell(m_order: int) -> int:
    """Per cell: 1 (k_steady) + 2 · ((m−1) (α free) + m (β free)) = 4m − 1.
    Layout: `[x_k, α_k_0..α_k_{m-2}, β_k_0..β_k_{m-1},
                  α_b_0..α_b_{m-2}, β_b_0..β_b_{m-1}]`."""
    return 1 + 2 * (2 * m_order - 1)


def total_free_params(m_order: int) -> int:
    return N_WHEELS * len(DIRS) * free_params_per_cell(m_order)


def _target_scale(k_steady: float, T: float) -> float:
    """Boundary-driven target: `Q(1) = target = √(k_steady) / T`. Used
    to normalise both α (Q polynomial coefs) and β (R polynomial coefs)
    so the identity vector is dimensionless `[1, 0, ..., 0]`."""
    k = max(0.0, k_steady)
    return math.sqrt(k) / T if T > 0.0 else 0.0


def _alpha_to_q(alpha_free: list[float], target: float) -> list[float]:
    """`Q_unit[i] = α_i · target` with `Σα = 1` enforced by deriving the
    last α from the free ones."""
    alpha_last = 1.0 - sum(alpha_free)
    return [a * target for a in (list(alpha_free) + [alpha_last])]


def _beta_to_r(beta_free: list[float], target: float) -> list[float]:
    """`R_unit[i] = β_i · target`. All m β values are free (no
    boundary constraint on r)."""
    return [b * target for b in beta_free]


def make_identity(
    m_order: int = DEFAULT_M_ORDER,
    max_motor: int = 60,
    kick_dur_ms: int = 100,
    brake_dur_ms: int = 100,
) -> CoefSet:
    """Identity baseline: `k_steady = 1`, all q / r polynomials are the
    constant `√(k)/T` in unit time. This produces the linear ramp
    `f_k(t) = (k/T_k)·t` and linear decay `f_b(t) = (k/T_b)·(T_b − t)`,
    matching the firmware's natural startup behaviour."""
    cs = CoefSet(
        m_order=m_order,
        max_motor=max_motor,
        kick_dur_ms=kick_dur_ms,
        brake_dur_ms=brake_dur_ms,
    )
    Tk_sec = kick_dur_ms / 1000.0
    Tb_sec = brake_dur_ms / 1000.0
    target_qk = _target_scale(1.0, Tk_sec)
    target_qb = _target_scale(1.0, Tb_sec)
    qk = [target_qk] + [0.0] * (m_order - 1)
    rk = [target_qk] + [0.0] * (m_order - 1)
    qb = [target_qb] + [0.0] * (m_order - 1)
    rb = [target_qb] + [0.0] * (m_order - 1)
    for w in range(N_WHEELS):
        for d in DIRS:
            cs.cells[(w, d)] = PerDirCoefs(
                k_steady=1.0,
                q_k=list(qk),
                r_k=list(rk),
                q_b=list(qb),
                r_b=list(rb),
            )
    return cs


def vector_to_coefs(v, template: CoefSet) -> CoefSet:
    """Build a CoefSet from a CMA-ES free-parameter vector. Length is
    `8 · (4m − 1)` with `m = template.m_order`."""
    import numpy as np
    m = template.m_order
    fpc = free_params_per_cell(m)
    expected = total_free_params(m)
    if len(v) != expected:
        raise ValueError(f"vector length {len(v)} != expected {expected}")
    v_arr = np.asarray(v, dtype=np.float64)
    cs = CoefSet(
        m_order=m,
        max_motor=template.max_motor,
        kick_dur_ms=template.kick_dur_ms,
        brake_dur_ms=template.brake_dur_ms,
    )
    Tk_sec = template.kick_dur_ms / 1000.0
    Tb_sec = template.brake_dur_ms / 1000.0
    n_alpha = m - 1
    n_beta = m
    for ci, key in enumerate(cs.cell_keys()):
        offset = ci * fpc
        x_k = float(v_arr[offset])
        k_steady = x_k * x_k
        idx = offset + 1
        alpha_k = [float(v_arr[idx + i]) for i in range(n_alpha)]
        idx += n_alpha
        beta_k = [float(v_arr[idx + i]) for i in range(n_beta)]
        idx += n_beta
        alpha_b = [float(v_arr[idx + i]) for i in range(n_alpha)]
        idx += n_alpha
        beta_b = [float(v_arr[idx + i]) for i in range(n_beta)]
        target_qk = _target_scale(k_steady, Tk_sec)
        target_qb = _target_scale(k_steady, Tb_sec)
        cs.cells[key] = PerDirCoefs(
            k_steady=k_steady,
            q_k=_alpha_to_q(alpha_k, target_qk),
            r_k=_beta_to_r(beta_k, target_qk),
            q_b=_alpha_to_q(alpha_b, target_qb),
            r_b=_beta_to_r(beta_b, target_qb),
        )
    return cs


def coefs_to_vector(cs: CoefSet):
    """Inverse of `vector_to_coefs`: read free parameters directly out
    of the canonical q/r polynomials. The α/β recovery divides by the
    target; the last α is dropped (determined by `Σα = 1`)."""
    import numpy as np
    m = cs.m_order
    fpc = free_params_per_cell(m)
    out = np.zeros(total_free_params(m), dtype=np.float64)
    Tk_sec = cs.kick_dur_ms / 1000.0
    Tb_sec = cs.brake_dur_ms / 1000.0
    n_alpha = m - 1
    n_beta = m
    for ci, key in enumerate(cs.cell_keys()):
        cell = cs.cells.get(key, PerDirCoefs())
        offset = ci * fpc
        out[offset] = math.sqrt(max(0.0, cell.k_steady))
        target_qk = _target_scale(cell.k_steady, Tk_sec)
        target_qb = _target_scale(cell.k_steady, Tb_sec)
        idx = offset + 1
        for i in range(n_alpha):
            qi = cell.q_k[i] if i < len(cell.q_k) else 0.0
            out[idx + i] = qi / target_qk if target_qk > 0.0 else 0.0
        idx += n_alpha
        for i in range(n_beta):
            ri = cell.r_k[i] if i < len(cell.r_k) else 0.0
            out[idx + i] = ri / target_qk if target_qk > 0.0 else 0.0
        idx += n_beta
        for i in range(n_alpha):
            qi = cell.q_b[i] if i < len(cell.q_b) else 0.0
            out[idx + i] = qi / target_qb if target_qb > 0.0 else 0.0
        idx += n_alpha
        for i in range(n_beta):
            ri = cell.r_b[i] if i < len(cell.r_b) else 0.0
            out[idx + i] = ri / target_qb if target_qb > 0.0 else 0.0
    return out


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------

JSON_VERSION = 4   # v4: even-Lukács with q_k, r_k, q_b, r_b; BRAKE input inversion


def load_json(path: Path | str) -> CoefSet:
    p = Path(path)
    obj = json.loads(p.read_text(encoding="utf-8"))
    version = int(obj.get("version", 0))
    if version != JSON_VERSION:
        raise ValueError(
            f"{p}: unsupported coef JSON version {version} "
            f"(expected {JSON_VERSION}). The motor model was reworked "
            "(even-degree Lukács with BRAKE input inversion: q² + t(T−t)·r²); "
            "regenerate identity via scripts/make_identity_coefs.py and recalibrate."
        )
    m_order = int(obj.get("m_order", DEFAULT_M_ORDER))
    if not (1 <= m_order <= M_MAX_ORDER):
        raise ValueError(f"{p}: m_order={m_order} out of range [1, {M_MAX_ORDER}]")
    cs = CoefSet(
        m_order=m_order,
        max_motor=int(obj.get("max_motor", 60)),
        kick_dur_ms=int(obj.get("kick_dur_ms", 100)),
        brake_dur_ms=int(obj.get("brake_dur_ms", 100)),
    )
    expected_len = m_order
    for entry in obj.get("cells", []):
        w = int(entry["wheel"])
        d = str(entry["dir"])
        if d not in DIRS or not (0 <= w < N_WHEELS):
            continue
        k_steady = float(entry.get("k_steady", 1.0))
        def _read(key: str, src: dict, length: int) -> list[float]:
            raw = [float(x) for x in src.get(key, _const_unit(length))]
            return (raw + [0.0] * length)[:length]
        cs.cells[(w, d)] = PerDirCoefs(
            k_steady=k_steady,
            q_k=_read("q_k", entry, expected_len),
            r_k=_read("r_k", entry, expected_len),
            q_b=_read("q_b", entry, expected_len),
            r_b=_read("r_b", entry, expected_len),
        )
    cs.normalize()
    return cs


def save_json(cs: CoefSet, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cells = []
    for key in cs.cell_keys():
        cell = cs.cells.get(key, PerDirCoefs(
            q_k=_const_unit(cs.m_order),
            r_k=_const_unit(cs.m_order),
            q_b=_const_unit(cs.m_order),
            r_b=_const_unit(cs.m_order),
        ))
        w, d = key
        cells.append({
            "wheel": w,
            "dir": d,
            "k_steady": cell.k_steady,
            "q_k": list(cell.q_k),
            "r_k": list(cell.r_k),
            "q_b": list(cell.q_b),
            "r_b": list(cell.r_b),
        })
    obj = {
        "version": JSON_VERSION,
        "m_order": cs.m_order,
        "max_motor": cs.max_motor,
        "kick_dur_ms": cs.kick_dur_ms,
        "brake_dur_ms": cs.brake_dur_ms,
        "cells": cells,
    }
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Binary push to firmware
# ---------------------------------------------------------------------------

def chunk_bytes(wheel: int, dir_idx: int, cell: PerDirCoefs,
                T_k_sec: float, T_b_sec: float) -> bytes:
    """Pack one (wheel, dir) cell into the 56-byte 0xC0 wire format. The
    monomial f_k(t) and f_b(t) are derived from q_k, r_k, q_b, r_b on
    the fly."""
    kick_t = _f_kick_monomial(cell.q_k, cell.r_k, T_k_sec)
    brake_t = _f_brake_monomial(cell.q_b, cell.r_b, T_b_sec)
    fmt = f"<BBBBf{POLY_NCOEFS}f{POLY_NCOEFS}f"
    buf = struct.pack(
        fmt,
        POLY_CHUNK_MAGIC, wheel, dir_idx, 0,
        float(cell.k_steady),
        *kick_t,
        *brake_t,
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
    Tk_sec = cs.kick_dur_ms / 1000.0
    Tb_sec = cs.brake_dur_ms / 1000.0
    sent = 0
    for w in range(N_WHEELS):
        for d_idx, d in enumerate(DIRS):
            cell = cs.cells.get((w, d), PerDirCoefs(
                q_k=_const_unit(cs.m_order),
                r_k=_const_unit(cs.m_order),
                q_b=_const_unit(cs.m_order),
                r_b=_const_unit(cs.m_order),
            ))
            buf = chunk_bytes(w, d_idx, cell, Tk_sec, Tb_sec)
            for _ in range(repeat):
                send_chunk(buf)
                sent += 1
                time.sleep(inter_chunk_ms / 1000.0)
    return sent
