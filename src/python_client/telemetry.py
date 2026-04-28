"""Receiver for the firmware's 25 Hz binary telemetry packets (magic 0xD0).

Wire format (49 B, must match `push_telemetry()` in roverc_server.ino):
  [0]      0xD0
  [1..4]   uint32 LE  millis()                       -> fw_t_ms
  [5..8]   float       gz_dps
  [9..12]  uint8[4]    phase   (PH_IDLE=0, KICK=1, STEADY=2, BRAKE=3)
  [13..28] float[4]    s_pre   (BRAKE-entry snapshot of normalised s, ∈ [-1,1])
  [29..32] int8[4]     motor   (commanded I2C value)
  [33..48] float[4]    s_norm  (current-tick normalised m_i)
"""
from __future__ import annotations

import struct
import threading
import time
from collections import deque
from dataclasses import dataclass

TEL_MAGIC = 0xD0
TEL_BYTES = 49

PHASE_NAMES = ("IDLE", "KICK", "STEADY", "BRAKE")


@dataclass(frozen=True)
class TelemetryPacket:
    pc_t: float           # PC monotonic seconds at receive
    fw_t_ms: int          # StickC millis() at send
    gz_dps: float         # raw gyro Z, deg/s
    phase: tuple[int, ...]    # 4 ints; per-wheel phase enum (PHASE_NAMES index)
    s_pre: tuple[float, ...]  # 4 floats; BRAKE snapshot per wheel
    motor: tuple[int, ...]    # 4 ints;   commanded I2C int8
    s_norm: tuple[float, ...] # 4 floats; current normalised s


def parse(raw: bytes, pc_t: float | None = None) -> TelemetryPacket | None:
    if len(raw) < TEL_BYTES or raw[0] != TEL_MAGIC:
        return None
    if pc_t is None:
        pc_t = time.monotonic()
    fw_t_ms, = struct.unpack_from("<I", raw, 1)
    gz_dps, = struct.unpack_from("<f", raw, 5)
    phase = struct.unpack_from("<4B", raw, 9)
    s_pre = struct.unpack_from("<4f", raw, 13)
    motor = struct.unpack_from("<4b", raw, 29)
    s_norm = struct.unpack_from("<4f", raw, 33)
    return TelemetryPacket(
        pc_t=float(pc_t),
        fw_t_ms=int(fw_t_ms),
        gz_dps=float(gz_dps),
        phase=tuple(int(x) for x in phase),
        s_pre=tuple(float(x) for x in s_pre),
        motor=tuple(int(x) for x in motor),
        s_norm=tuple(float(x) for x in s_norm),
    )


class TelemetryQueue:
    """Thread-safe ring buffer fed from RoverCClient's rx loop. The typical
    pattern in calibrate.py is `drain()` once per trial to grab everything
    that arrived during the trial window."""

    def __init__(self, maxlen: int = 5000) -> None:
        self._lock = threading.Lock()
        self._packets: deque[TelemetryPacket] = deque(maxlen=maxlen)

    def on_packet(self, raw: bytes) -> None:
        pkt = parse(raw)
        if pkt is None:
            return
        with self._lock:
            self._packets.append(pkt)

    def drain(self) -> list[TelemetryPacket]:
        with self._lock:
            out = list(self._packets)
            self._packets.clear()
            return out

    def latest(self) -> TelemetryPacket | None:
        with self._lock:
            return self._packets[-1] if self._packets else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._packets)
