#!/usr/bin/env python3
"""Offline visualizer for the RoverC per-(wheel, dir) motor model.

This talks to no hardware. It takes one set of motor-cell parameters and
draws the response curve they produce, so you can see the shape before
writing the numbers into config.json's [motor] section. The response is
cell-agnostic: it depends only on the parameter values, not on which wheel
or direction the cell belongs to.

The plotted gain is the multiplier the firmware applies to the normalised
command `s`, swept across one KICK -> STEADY -> BRAKE cycle:

    KICK   : gain = f_k(t)     on [0, T_k]   (rises 0 -> k_steady)
    STEADY : gain = k_steady   (flat)
    BRAKE  : gain = f_b(t)     on [0, T_b]   (falls k_steady -> 0)

The KICK / BRAKE shapes are set by the boundary-pinning parameters alpha /
beta (not raw q/r coefficients): Sum(alpha) = 1 forces f_k(T_k) = k_steady
and f_b(0) = k_steady for any slider values, so the curve is always
continuous across phase boundaries (no height jump). The dumped JSON carries
the same alpha/beta fields config.json expects, so it pastes in directly.

The monomial f_k / f_b are taken from `coefs.chunk_bytes` -- the exact bytes
the firmware would receive -- so the curve matches on-device behaviour. A
red line marks the saturation threshold gain = 127 / max_motor; wherever the
curve rises above it the signed int8 motor command clamps.

Built on pygame (already a project dependency) and the teleop Slider/Button
widgets -- no extra GUI toolkit.

Usage:
    uv run examples/motor_tuner/motor_tuner.py            # m_order = 2
    uv run examples/motor_tuner/motor_tuner.py --m-order 1
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import pygame

# Make the shared crover_mod library and the teleop widgets importable
# without installing anything (same sys.path trick teleop/scripts use).
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src" / "crover_mod"))
sys.path.insert(0, str(_ROOT / "examples" / "teleop"))

from coefs import (  # noqa: E402
    POLY_NCOEFS,
    PerDirCoefs,
    build_polys,
    chunk_bytes,
)
from widgets import Button, Slider  # noqa: E402

WINDOW_SIZE = (1040, 760)
PLOT_RECT = pygame.Rect(64, 48, 912, 320)
BG = (18, 18, 24)
# Fixed STEADY window used only for drawing the plateau (the real STEADY
# phase lasts as long as the command is held; it has no tunable duration).
STEADY_VIS_MS = 120
SAMPLES_PER_PHASE = 120


def _eval_monomial(coefs: list[float], t_sec: float) -> float:
    return sum(c * t_sec**i for i, c in enumerate(coefs))


def cell_monomials(cell: PerDirCoefs) -> tuple[list[float], list[float]]:
    """Unpack the kick / brake monomials (in t) from the exact 0xC0 wire
    bytes the firmware would receive for this cell."""
    buf = chunk_bytes(0, 0, cell)
    vals = struct.unpack(f"<BBBBf{POLY_NCOEFS}f{POLY_NCOEFS}fHH", buf)
    kick_c = list(vals[5:5 + POLY_NCOEFS])
    brake_c = list(vals[5 + POLY_NCOEFS:5 + 2 * POLY_NCOEFS])
    return kick_c, brake_c


def response_curve(cell: PerDirCoefs) -> tuple[list[float], list[float], list[float]]:
    """Return (time_ms, gain, phase_boundaries_ms) across KICK->STEADY->BRAKE."""
    kick_c, brake_c = cell_monomials(cell)
    Tk = cell.kick_dur_ms / 1000.0
    Tb = cell.brake_dur_ms / 1000.0

    ts: list[float] = []
    gains: list[float] = []

    for i in range(SAMPLES_PER_PHASE + 1):
        t = Tk * i / SAMPLES_PER_PHASE
        ts.append(t * 1000.0)
        gains.append(_eval_monomial(kick_c, t))

    base = cell.kick_dur_ms
    for i in range(SAMPLES_PER_PHASE + 1):
        ts.append(base + STEADY_VIS_MS * i / SAMPLES_PER_PHASE)
        gains.append(cell.k_steady)

    base = cell.kick_dur_ms + STEADY_VIS_MS
    for i in range(SAMPLES_PER_PHASE + 1):
        t = Tb * i / SAMPLES_PER_PHASE
        ts.append(base + t * 1000.0)
        gains.append(_eval_monomial(brake_c, t))

    boundaries = [float(cell.kick_dur_ms), float(cell.kick_dur_ms + STEADY_VIS_MS)]
    return ts, gains, boundaries


def _draw_plot(
    surf: pygame.Surface, ts: list[float], gains: list[float],
    boundaries: list[float], k_steady: float, max_motor: float,
    font: pygame.font.Font,
) -> None:
    r = PLOT_RECT
    pygame.draw.rect(surf, (28, 28, 36), r)
    pygame.draw.rect(surf, (80, 80, 95), r, width=1)

    t0, t1 = ts[0], ts[-1]
    sat = (127.0 / max_motor) if max_motor > 0 else None
    gmax = max(gains + [k_steady] + ([sat] if sat is not None else []))
    gmin = min(gains + [0.0])
    span = max(1e-6, gmax - gmin)
    gmax += 0.08 * span
    gmin -= 0.08 * span
    span = gmax - gmin

    def px(t: float) -> int:
        return int(r.x + (t - t0) / max(1e-6, t1 - t0) * r.width)

    def py(g: float) -> int:
        return int(r.bottom - (g - gmin) / span * r.height)

    # y=0 axis and k_steady reference.
    if gmin <= 0.0 <= gmax:
        y0 = py(0.0)
        pygame.draw.line(surf, (70, 70, 85), (r.x, y0), (r.right, y0), 1)
    yk = py(k_steady)
    pygame.draw.line(surf, (90, 90, 70), (r.x, yk), (r.right, yk), 1)
    surf.blit(font.render(f"k_steady={k_steady:.2f}", True, (170, 170, 130)),
              (r.x + 4, yk - 16))

    # Saturation threshold (int8 clamps above this gain).
    if sat is not None and gmin <= sat <= gmax:
        ys = py(sat)
        pygame.draw.line(surf, (200, 80, 80), (r.x, ys), (r.right, ys), 1)
        surf.blit(font.render(f"int8 clamp @ gain={sat:.2f} (=127/{int(max_motor)})",
                              True, (210, 110, 110)), (r.right - 260, ys - 16))

    # Phase boundaries + labels.
    labels = [("KICK", t0), ("STEADY", boundaries[0]), ("BRAKE", boundaries[1])]
    for b in boundaries:
        xb = px(b)
        pygame.draw.line(surf, (60, 60, 75), (xb, r.y), (xb, r.bottom), 1)
    for name, start in labels:
        surf.blit(font.render(name, True, (150, 150, 165)), (px(start) + 4, r.y + 4))

    # Gain curve.
    pts = [(px(t), py(g)) for t, g in zip(ts, gains)]
    if len(pts) > 1:
        pygame.draw.aalines(surf, (90, 160, 230), False, pts)

    # Axis labels.
    surf.blit(font.render("gain (p/s)", True, (180, 180, 190)), (r.x, r.y - 18))
    surf.blit(font.render(f"time [ms]  0 .. {int(t1)}", True, (180, 180, 190)),
              (r.x, r.bottom + 6))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--m-order", type=int, default=2, choices=(1, 2),
                    help="Polynomial coefficient length per q/r (default 2).")
    args = ap.parse_args()
    m = args.m_order

    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE)
    pygame.display.set_caption("RoverC motor response (offline)")
    font = pygame.font.SysFont("monospace", 13)
    big_font = pygame.font.SysFont("monospace", 15)
    clock = pygame.time.Clock()

    # Boundary-pinning parameters (alpha / beta), not raw q/r coefficients:
    # Sum(alpha) = 1 keeps f_k(T_k) = k_steady and f_b(0) = k_steady, so the
    # curve is always continuous across phase transitions. There are m-1 free
    # alpha (the last is determined) and m free beta per phase. Identity =
    # alpha[0]=1, beta[0]=1, rest 0.
    def alpha_specs(ph: str) -> list[tuple[str, str, float, float, float, float]]:
        return [(f"alpha_{ph}{i}", f"alpha[{i}]", -0.5, 2.0, 1.0, 0.05)
                for i in range(m - 1)]

    def beta_specs(ph: str) -> list[tuple[str, str, float, float, float, float]]:
        return [(f"beta_{ph}{i}", f"beta[{i}]", -2.0, 2.0, (1.0 if i == 0 else 0.0), 0.05)
                for i in range(m)]

    # Columns map to the plot's left-to-right phase order: KICK on the left,
    # STEADY (+ global max_motor) in the middle, BRAKE on the right.
    columns: list[tuple[str, int, list[tuple[str, str, float, float, float, float]]]] = [
        ("KICK", 64, [("kick_dur_ms", "kick_dur_ms", 10.0, 500.0, 100.0, 5.0),
                      *alpha_specs("kick"), *beta_specs("kick")]),
        ("STEADY / global", 408, [("k_steady", "k_steady", 0.0, 3.0, 1.0, 0.0),
                                  ("max_motor", "max_motor", 0.0, 127.0, 60.0, 1.0)]),
        ("BRAKE", 752, [("brake_dur_ms", "brake_dur_ms", 10.0, 500.0, 100.0, 5.0),
                        *alpha_specs("brake"), *beta_specs("brake")]),
    ]
    header_y, row0, row_step, slider_w = 418, 452, 46, 240
    column_headers = [(title, x) for title, x, _ in columns]

    sliders: dict[str, Slider] = {}
    for _title, x, items in columns:
        for row, (key, label, lo, hi, init, step) in enumerate(items):
            fmt = "{:.0f}" if step >= 1.0 else "{:.2f}"
            sliders[key] = Slider(
                pygame.Rect(x, row0 + row * row_step, slider_w, 12),
                label, lo, hi, init, step=step, fmt=fmt)

    dump_btn = Button(pygame.Rect(WINDOW_SIZE[0] - 196, WINDOW_SIZE[1] - 44, 176, 30),
                      "print cell JSON")

    def cell_params() -> dict:
        """The raw slider state in config.json's alpha/beta cell form."""
        return {
            "k_steady": round(sliders["k_steady"].value, 4),
            "kick_dur_ms": int(sliders["kick_dur_ms"].value),
            "brake_dur_ms": int(sliders["brake_dur_ms"].value),
            "alpha_kick": [round(sliders[f"alpha_kick{i}"].value, 4) for i in range(m - 1)],
            "beta_kick": [round(sliders[f"beta_kick{i}"].value, 4) for i in range(m)],
            "alpha_brake": [round(sliders[f"alpha_brake{i}"].value, 4) for i in range(m - 1)],
            "beta_brake": [round(sliders[f"beta_brake{i}"].value, 4) for i in range(m)],
        }

    def current_cell() -> PerDirCoefs:
        p = cell_params()
        q_k, r_k = build_polys(p["k_steady"], p["kick_dur_ms"] / 1000.0,
                               p["alpha_kick"], p["beta_kick"])
        q_b, r_b = build_polys(p["k_steady"], p["brake_dur_ms"] / 1000.0,
                               p["alpha_brake"], p["beta_brake"])
        return PerDirCoefs(
            k_steady=p["k_steady"],
            kick_dur_ms=p["kick_dur_ms"],
            brake_dur_ms=p["brake_dur_ms"],
            q_k=q_k, r_k=r_k, q_b=q_b, r_b=r_b,
        )

    def dump() -> None:
        print(json.dumps(cell_params()))

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            else:
                for sl in sliders.values():
                    sl.handle_event(event)
                if dump_btn.handle_event(event):
                    dump()

        cell = current_cell()
        ts, gains, boundaries = response_curve(cell)

        screen.fill(BG)
        _draw_plot(screen, ts, gains, boundaries, cell.k_steady,
                   sliders["max_motor"].value, font)
        for title, hx in column_headers:
            screen.blit(big_font.render(title, True, (205, 205, 150)), (hx, header_y))
        for sl in sliders.values():
            sl.draw(screen, font)
        dump_btn.draw(screen, big_font)
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
