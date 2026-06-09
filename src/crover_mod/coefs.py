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

Free-parameter layout per `(wheel, dir)`:
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

The table is built from the `motor` section of `config.json` via
`from_config`; there is no separate on-disk coefficient file.
"""
from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass, field
from math import comb
from typing import Callable

DIRS = ("fwd", "bwd")
WHEEL_NAMES = ("FL", "FR", "RL", "RR")
N_WHEELS = 4

# config.json `motor` section uses human-readable per-cell keys
# `<wheel>_<dir>`, e.g. `front_left_fwd`, `rear_right_back`. These map to the
# canonical (wheel index, internal dir) pair.
WHEEL_CONFIG_NAMES = ("front_left", "front_right", "rear_left", "rear_right")
DIR_CONFIG_SUFFIX = {"fwd": "fwd", "back": "bwd"}
# Keys under `motor` that are scalars, not per-cell coefficient objects.
# (kick_dur_ms / brake_dur_ms are per-cell, carried inside each cell object.)
MOTOR_SCALAR_KEYS = frozenset({"m_order", "max_motor"})
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
#   [56..57] kick_dur_ms            (uint16 LE; per-cell KICK phase length)
#   [58..59] brake_dur_ms           (uint16 LE; per-cell BRAKE phase length)
# Total 60 bytes. 8 chunks (4 wheels × 2 dirs) = full coef table.
POLY_CHUNK_MAGIC = 0xC0
POLY_CHUNK_BYTES = 4 + 4 + POLY_NCOEFS * 4 + POLY_NCOEFS * 4 + 2 + 2   # = 60

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

    `kick_dur_ms` / `brake_dur_ms` are the KICK / BRAKE phase lengths for
    this specific (wheel, dir) cell — durations are per-motor, not global.

    Boundary conditions are not enforced by this class; callers go
    through `make_identity` to get a
    constraint-respecting set."""
    k_steady: float = 1.0
    kick_dur_ms: int = 100
    brake_dur_ms: int = 100
    q_k: list[float] = field(default_factory=lambda: [1.0])
    r_k: list[float] = field(default_factory=lambda: [1.0])
    q_b: list[float] = field(default_factory=lambda: [1.0])
    r_b: list[float] = field(default_factory=lambda: [1.0])


@dataclass
class CoefSet:
    """Full coefficient table plus the scalar max-motor setting. Phase
    durations are per-cell (see `PerDirCoefs`), not stored here."""
    m_order: int = DEFAULT_M_ORDER
    max_motor: int = 60
    cells: dict[CellKey, PerDirCoefs] = field(default_factory=dict)

    @staticmethod
    def cell_keys() -> list[CellKey]:
        """Canonical cell ordering used for vector packing and binary push."""
        return [(w, d) for w in range(N_WHEELS) for d in DIRS]


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
# Identity / baseline coefficient construction
# ---------------------------------------------------------------------------

def _target_scale(k_steady: float, T: float) -> float:
    """Boundary-driven target: `Q(1) = target = √(k_steady) / T`. Used
    to normalise both α (Q polynomial coefs) and β (R polynomial coefs)
    so the identity vector is dimensionless `[1, 0, ..., 0]`."""
    k = max(0.0, k_steady)
    return math.sqrt(k) / T if T > 0.0 else 0.0


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
    cs = CoefSet(m_order=m_order, max_motor=max_motor)
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
                kick_dur_ms=kick_dur_ms,
                brake_dur_ms=brake_dur_ms,
                q_k=list(qk),
                r_k=list(rk),
                q_b=list(qb),
                r_b=list(rb),
            )
    return cs


# ---------------------------------------------------------------------------
# config.json -> CoefSet
# ---------------------------------------------------------------------------

def build_polys(
    k_steady: float, T_sec: float, alpha_free: list[float], beta: list[float],
) -> tuple[list[float], list[float]]:
    """Reconstruct the unit-time q / r polynomials from the boundary-pinning
    parameters alpha / beta.

    With `target = sqrt(k_steady) / T` and `Sum(alpha) = 1` (the last alpha is
    determined by the others), `Q(1) = target = sqrt(k)/T`, which forces
    `f_k(T_k) = k_steady` (and by input-inversion symmetry `f_b(0) = k_steady`)
    for any alpha/beta -- so the response is always continuous across a phase
    boundary. `beta` is unconstrained (the `t(T-t)` term vanishes at the
    boundary). `alpha_free` has `m_order - 1` entries, `beta` has `m_order`."""
    target = math.sqrt(max(0.0, k_steady)) / T_sec if T_sec > 0.0 else 0.0
    alpha = [*alpha_free, 1.0 - sum(alpha_free)]
    q_unit = [a * target for a in alpha]
    r_unit = [b * target for b in beta]
    return q_unit, r_unit


def from_config(cfg: dict) -> CoefSet:
    """Build the motor coefficient table from a parsed `config.json` dict.

    Every motor parameter must be present in the `motor` section — there are
    no defaults. The scalars are `max_motor` and `m_order`. The per-cell
    coefficients are named `<wheel>_<dir>` keys directly under `motor`, where
    `<wheel>` is `front_left` / `front_right` / `rear_left` / `rear_right` and
    `<dir>` is `fwd` / `back`. All eight cells must be present, each fully
    specified — including its own `kick_dur_ms` / `brake_dur_ms` (phase
    lengths are per-motor)::

        "motor": {
          "max_motor": 60, "m_order": 2,
          "front_left_fwd": {"k_steady": 1.0,
                             "kick_dur_ms": 100, "brake_dur_ms": 100,
                             "alpha_kick": [...],  "beta_kick": [...],
                             "alpha_brake": [...], "beta_brake": [...]},
          ... (all 8 cells)
        }

    The shape is given by the boundary-pinning parameters alpha / beta (see
    `build_polys`), not raw q/r: `alpha_kick` / `alpha_brake` have `m_order-1`
    entries (the last alpha is determined by `Sum(alpha)=1`, which pins the
    phase-boundary height to `k_steady`), and `beta_kick` / `beta_brake` have
    `m_order` entries. `config.json` is the single, fully-explicit source of
    motor characteristics — there is no separate coefficient file.
    """
    if "motor" not in cfg:
        raise ValueError("config has no [motor] section")
    motor = cfg["motor"]
    m_order = int(motor["m_order"])
    if not (1 <= m_order <= M_MAX_ORDER):
        raise ValueError(f"motor.m_order={m_order} out of range [1, {M_MAX_ORDER}]")
    cs = CoefSet(m_order=m_order, max_motor=int(motor["max_motor"]))

    def _vec(entry: dict, name: str, length: int, cell: str) -> list[float]:
        raw = [float(x) for x in entry[name]]
        if len(raw) != length:
            raise ValueError(
                f"motor.{cell}.{name} must have {length} entries, got {len(raw)}"
            )
        return raw

    valid_cell_keys = {
        f"{wname}_{suffix}"
        for wname in WHEEL_CONFIG_NAMES
        for suffix in DIR_CONFIG_SUFFIX
    }
    seen: set[CellKey] = set()
    for key in motor:
        if key in MOTOR_SCALAR_KEYS:
            continue
        if key not in valid_cell_keys:
            raise ValueError(f"motor.{key}: unknown key (expected a scalar or "
                             f"a <wheel>_<dir> cell)")
        wname, suffix = key.rsplit("_", 1)
        w = WHEEL_CONFIG_NAMES.index(wname)
        d = DIR_CONFIG_SUFFIX[suffix]
        entry = motor[key]
        k_steady = float(entry["k_steady"])
        kick_dur = int(entry["kick_dur_ms"])
        brake_dur = int(entry["brake_dur_ms"])
        q_k, r_k = build_polys(
            k_steady, kick_dur / 1000.0,
            _vec(entry, "alpha_kick", m_order - 1, key),
            _vec(entry, "beta_kick", m_order, key),
        )
        q_b, r_b = build_polys(
            k_steady, brake_dur / 1000.0,
            _vec(entry, "alpha_brake", m_order - 1, key),
            _vec(entry, "beta_brake", m_order, key),
        )
        cs.cells[(w, d)] = PerDirCoefs(
            k_steady=k_steady, kick_dur_ms=kick_dur, brake_dur_ms=brake_dur,
            q_k=q_k, r_k=r_k, q_b=q_b, r_b=r_b,
        )
        seen.add((w, d))
    missing = [k for k in valid_cell_keys
               if (WHEEL_CONFIG_NAMES.index(k.rsplit("_", 1)[0]),
                   DIR_CONFIG_SUFFIX[k.rsplit("_", 1)[1]]) not in seen]
    if missing:
        raise ValueError(f"motor: missing cells {sorted(missing)}")
    return cs


# ---------------------------------------------------------------------------
# Binary push to firmware
# ---------------------------------------------------------------------------

def chunk_bytes(wheel: int, dir_idx: int, cell: PerDirCoefs) -> bytes:
    """Pack one (wheel, dir) cell into the 60-byte 0xC0 wire format. The
    monomial f_k(t) and f_b(t) are derived from q_k, r_k, q_b, r_b on the
    fly using this cell's own kick/brake durations, which are also carried
    in the chunk so the firmware can time the phase transitions per-motor."""
    T_k_sec = cell.kick_dur_ms / 1000.0
    T_b_sec = cell.brake_dur_ms / 1000.0
    kick_t = _f_kick_monomial(cell.q_k, cell.r_k, T_k_sec)
    brake_t = _f_brake_monomial(cell.q_b, cell.r_b, T_b_sec)
    fmt = f"<BBBBf{POLY_NCOEFS}f{POLY_NCOEFS}fHH"
    buf = struct.pack(
        fmt,
        POLY_CHUNK_MAGIC, wheel, dir_idx, 0,
        float(cell.k_steady),
        *kick_t,
        *brake_t,
        int(cell.kick_dur_ms),
        int(cell.brake_dur_ms),
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
    """Push scalar cfg (max_motor) + 8 binary chunks. Each chunk carries its
    own kick/brake durations, so no global duration is sent. Inter-chunk delay
    matches the ESP32 LWIP UDP rx queue depth (~6-8 packets); duplication is
    the same durability pattern that `RoverCClient.send_config_dict(repeat=3)`
    uses for the JSON cfg envelope. Returns total chunk transmissions
    (8 × repeat). Callbacks let us avoid importing roverc here (no
    circular dependency)."""
    send_cfg_dict({"mx": int(cs.max_motor)})
    time.sleep(0.02)
    sent = 0
    for w in range(N_WHEELS):
        for d_idx, d in enumerate(DIRS):
            cell = cs.cells.get((w, d), PerDirCoefs(
                q_k=_const_unit(cs.m_order),
                r_k=_const_unit(cs.m_order),
                q_b=_const_unit(cs.m_order),
                r_b=_const_unit(cs.m_order),
            ))
            buf = chunk_bytes(w, d_idx, cell)
            for _ in range(repeat):
                send_chunk(buf)
                sent += 1
                time.sleep(inter_chunk_ms / 1000.0)
    return sent
