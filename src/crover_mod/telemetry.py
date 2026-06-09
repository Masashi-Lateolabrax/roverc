"""Receiver for the firmware's 25 Hz binary telemetry packets (magic 0xD2).

Wire format (73 B, must match `push_telemetry()` in roverc_server.ino):
  [0]      0xD2
  [1..4]   uint32 LE  millis()                       -> fw_t_ms
  [5..8]   float       gx_dps
  [9..12]  float       gy_dps
  [13..16] float       gz_dps
  [17..20] float       ax_g    (M5Unified default unit: g, 1.0 == 9.81 m/s^2)
  [21..24] float       ay_g
  [25..28] float       az_g
  [29..32] uint8[4]    phase   (PH_IDLE=0, KICK=1, STEADY=2, BRAKE=3)
  [33..48] float[4]    s_pre   (BRAKE-entry snapshot of normalised s, in [-1,1])
  [49..52] int8[4]     motor   (commanded I2C value)
  [53..68] float[4]    s_norm  (current-tick normalised m_i)
  [69..70] uint16 LE   vbat_mv (StickC battery voltage, mV)
  [71]     uint8       bat_pct (0..100; 0xFF == unknown)
  [72]     uint8       charging (0=no, 1=yes; 0xFF == unknown)
"""
from __future__ import annotations

import struct
import threading
import time
from collections import deque
from dataclasses import dataclass

TEL_MAGIC = 0xD2
TEL_BYTES = 73

PHASE_NAMES = ("IDLE", "KICK", "STEADY", "BRAKE")


@dataclass(frozen=True)
class TelemetryPacket:
    pc_t: float           # PC monotonic seconds at receive
    fw_t_ms: int          # StickC millis() at send
    gx_dps: float         # raw gyro X, deg/s
    gy_dps: float         # raw gyro Y, deg/s
    gz_dps: float         # raw gyro Z, deg/s
    ax_g: float           # raw accel X, g
    ay_g: float           # raw accel Y, g
    az_g: float           # raw accel Z, g
    phase: tuple[int, ...]    # 4 ints; per-wheel phase enum (PHASE_NAMES index)
    s_pre: tuple[float, ...]  # 4 floats; BRAKE snapshot per wheel
    motor: tuple[int, ...]    # 4 ints;   commanded I2C int8
    s_norm: tuple[float, ...] # 4 floats; current normalised s
    vbat_mv: int          # StickC battery, mV (0 if unreadable)
    bat_pct: int | None   # 0..100, or None if unknown
    charging: bool | None # True/False, or None if unknown


def parse(raw: bytes, pc_t: float | None = None) -> TelemetryPacket | None:
    if len(raw) < TEL_BYTES or raw[0] != TEL_MAGIC:
        return None
    if pc_t is None:
        pc_t = time.monotonic()
    fw_t_ms, = struct.unpack_from("<I", raw, 1)
    gx_dps, gy_dps, gz_dps = struct.unpack_from("<3f", raw, 5)
    ax_g, ay_g, az_g = struct.unpack_from("<3f", raw, 17)
    phase = struct.unpack_from("<4B", raw, 29)
    s_pre = struct.unpack_from("<4f", raw, 33)
    motor = struct.unpack_from("<4b", raw, 49)
    s_norm = struct.unpack_from("<4f", raw, 53)
    vbat_mv, bat_pct_raw, charging_raw = struct.unpack_from("<HBB", raw, 69)
    bat_pct = None if bat_pct_raw == 0xFF else int(bat_pct_raw)
    if charging_raw == 0xFF:
        charging: bool | None = None
    else:
        charging = bool(charging_raw)
    return TelemetryPacket(
        pc_t=float(pc_t),
        fw_t_ms=int(fw_t_ms),
        gx_dps=float(gx_dps),
        gy_dps=float(gy_dps),
        gz_dps=float(gz_dps),
        ax_g=float(ax_g),
        ay_g=float(ay_g),
        az_g=float(az_g),
        phase=tuple(int(x) for x in phase),
        s_pre=tuple(float(x) for x in s_pre),
        motor=tuple(int(x) for x in motor),
        s_norm=tuple(float(x) for x in s_norm),
        vbat_mv=int(vbat_mv),
        bat_pct=bat_pct,
        charging=charging,
    )


class TelemetryQueue:
    """Thread-safe ring buffer fed from RoverCClient's rx loop. Currently
    unused (it backed the removed CMA-ES calibrator, which drained it once
    per trial); kept as a general-purpose buffer for future batch consumers."""

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
