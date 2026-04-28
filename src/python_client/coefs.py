"""Polynomial motor-correction coefficients for the RoverC firmware.

Mirrors the firmware-side `WheelCoefs` layout (see `roverc_server.ino`):

  per (wheel ∈ {FL,FR,RL,RR}, dir ∈ {fwd,bwd}, phase ∈ {kick,steady,brake})
    f(s, t) = Σ a[j][k] · s^j · t^k       j,k ∈ {0..3}
    g(s, t) = Σ b[j][k] · s^j · t^k
    p_norm  = s · f(s, t) + g(s, t)

24 cells × 32 floats = 768 free parameters total. JSON is the on-disk format
(human-readable, git-friendly); transport to firmware is binary 132-byte
chunks (magic 0xC0) so we don't pay JSON encode/parse and ESP32 PSRAM cost.
"""
from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

DIRS = ("fwd", "bwd")
PHASES = ("kick", "steady", "brake")
WHEEL_NAMES = ("FL", "FR", "RL", "RR")
N_WHEELS = 4
POLY_ORDER = 4  # j, k ∈ {0..3}, so 4 × 4 = 16 monomials per polynomial

# Binary chunk wire format (must match firmware constants in roverc_server.ino).
POLY_CHUNK_MAGIC = 0xC0
POLY_CHUNK_BYTES = 132

CellKey = tuple[int, str, str]   # (wheel 0..3, dir, phase)


def _zeros4x4() -> list[list[float]]:
    return [[0.0] * 4 for _ in range(4)]


@dataclass
class Poly:
    """Bivariate polynomial cell. `a` parameterises f(s,t), `b` parameterises
    g(s,t); both are 4x4 row-major (a[j][k] is the s^j · t^k coefficient)."""
    a: list[list[float]] = field(default_factory=_zeros4x4)
    b: list[list[float]] = field(default_factory=_zeros4x4)


@dataclass
class CoefSet:
    """Full polynomial table plus the scalar phase-duration / max-motor
    settings that the firmware also needs."""
    max_motor: int = 60
    kick_dur_ms: int = 100
    brake_dur_ms: int = 100
    cells: dict[CellKey, Poly] = field(default_factory=dict)

    @staticmethod
    def cell_keys() -> list[CellKey]:
        """Canonical cell ordering used for vector packing and binary push."""
        return [(w, d, p)
                for w in range(N_WHEELS)
                for d in DIRS
                for p in PHASES]

    def normalize(self) -> None:
        """Ensure all 24 cells exist; missing ones become zero polynomials."""
        for key in self.cell_keys():
            if key not in self.cells:
                self.cells[key] = Poly()


def make_identity(
    max_motor: int = 60,
    kick_dur_ms: int = 100,
    brake_dur_ms: int = 100,
) -> CoefSet:
    """Reproduces the firmware default: KICK / STEADY are p_norm = s
    (a[0][0] = 1, all else 0). BRAKE is fully zero (no active brake)."""
    cs = CoefSet(
        max_motor=max_motor,
        kick_dur_ms=kick_dur_ms,
        brake_dur_ms=brake_dur_ms,
    )
    for w in range(N_WHEELS):
        for d in DIRS:
            kick = Poly()
            kick.a[0][0] = 1.0
            steady = Poly()
            steady.a[0][0] = 1.0
            cs.cells[(w, d, "kick")] = kick
            cs.cells[(w, d, "steady")] = steady
            cs.cells[(w, d, "brake")] = Poly()
    return cs


def load_json(path: Path | str) -> CoefSet:
    p = Path(path)
    obj = json.loads(p.read_text(encoding="utf-8"))
    cs = CoefSet(
        max_motor=int(obj.get("max_motor", 60)),
        kick_dur_ms=int(obj.get("kick_dur_ms", 100)),
        brake_dur_ms=int(obj.get("brake_dur_ms", 100)),
    )
    for entry in obj.get("cells", []):
        w = int(entry["wheel"])
        d = str(entry["dir"])
        ph = str(entry["phase"])
        if d not in DIRS or ph not in PHASES or not (0 <= w < N_WHEELS):
            continue
        a_raw = entry.get("a") or _zeros4x4()
        b_raw = entry.get("b") or _zeros4x4()
        cs.cells[(w, d, ph)] = Poly(
            a=[[float(x) for x in row] for row in a_raw],
            b=[[float(x) for x in row] for row in b_raw],
        )
    cs.normalize()
    return cs


def save_json(cs: CoefSet, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cells = []
    for key in cs.cell_keys():
        poly = cs.cells.get(key, Poly())
        w, d, ph = key
        cells.append({
            "wheel": w,
            "dir": d,
            "phase": ph,
            "a": poly.a,
            "b": poly.b,
        })
    obj = {
        "version": 1,
        "max_motor": cs.max_motor,
        "kick_dur_ms": cs.kick_dur_ms,
        "brake_dur_ms": cs.brake_dur_ms,
        "cells": cells,
    }
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def chunk_bytes(wheel: int, dir_idx: int, phase_idx: int, poly: Poly) -> bytes:
    """Pack one polynomial cell into the 132-byte 0xC0 wire format."""
    flat_a = [poly.a[j][k] for j in range(4) for k in range(4)]
    flat_b = [poly.b[j][k] for j in range(4) for k in range(4)]
    buf = struct.pack(
        "<BBBB16f16f",
        POLY_CHUNK_MAGIC, wheel, dir_idx, phase_idx,
        *flat_a, *flat_b,
    )
    assert len(buf) == POLY_CHUNK_BYTES
    return buf


def push_to_firmware(
    cs: CoefSet,
    send_chunk: Callable[[bytes], None],
    send_cfg_dict: Callable[[dict], None],
    repeat: int = 2,
    inter_chunk_ms: float = 8.0,
) -> int:
    """Blast scalar cfg + 24 binary chunks. Inter-chunk delay matches the
    ESP32 LWIP UDP rx queue depth (~6-8 packets); duplication is the same
    durability pattern that `RoverCClient.send_config(repeat=3)` uses for the
    JSON cfg envelope. Returns total chunk transmissions (24 × repeat).
    Callbacks let us avoid importing roverc here (no circular dependency)."""
    send_cfg_dict({
        "mx": int(cs.max_motor),
        "kdur": int(cs.kick_dur_ms),
        "bdur": int(cs.brake_dur_ms),
    })
    time.sleep(0.02)
    sent = 0
    for w in range(N_WHEELS):
        for d_idx, d in enumerate(DIRS):
            for p_idx, ph in enumerate(PHASES):
                poly = cs.cells.get((w, d, ph), Poly())
                buf = chunk_bytes(w, d_idx, p_idx, poly)
                for _ in range(repeat):
                    send_chunk(buf)
                    sent += 1
                    time.sleep(inter_chunk_ms / 1000.0)
    return sent


def coefs_to_vector(cs: CoefSet):
    """Flatten to a length-768 numpy vector for CMA-ES. Order is the same
    canonical cell order, then 16 a-floats + 16 b-floats per cell."""
    import numpy as np
    v = np.zeros(len(cs.cell_keys()) * 32, dtype=np.float64)
    for ci, key in enumerate(cs.cell_keys()):
        poly = cs.cells.get(key, Poly())
        offset = ci * 32
        for j in range(4):
            for k in range(4):
                v[offset + j * 4 + k] = poly.a[j][k]
                v[offset + 16 + j * 4 + k] = poly.b[j][k]
    return v


def vector_to_coefs(v, template: CoefSet) -> CoefSet:
    """Inverse of `coefs_to_vector`. Inherits scalar fields from `template`."""
    cs = CoefSet(
        max_motor=template.max_motor,
        kick_dur_ms=template.kick_dur_ms,
        brake_dur_ms=template.brake_dur_ms,
    )
    keys = cs.cell_keys()
    expected = len(keys) * 32
    if len(v) != expected:
        raise ValueError(f"vector length {len(v)} != expected {expected}")
    for ci, key in enumerate(keys):
        offset = ci * 32
        a = [[float(v[offset + j * 4 + k]) for k in range(4)] for j in range(4)]
        b = [[float(v[offset + 16 + j * 4 + k]) for k in range(4)] for j in range(4)]
        cs.cells[key] = Poly(a=a, b=b)
    return cs
