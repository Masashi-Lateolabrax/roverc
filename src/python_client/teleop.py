#!/usr/bin/env python3
"""Keyboard teleop for RoverC via the StickC Plus2 UDP server.

Pygame UI: real KEYDOWN/KEYUP keys, per-wheel sliders laid out spatially in a
2x2 grid (front-left top-left etc.), and an Apply button that pushes the
configuration to the server. The server runs the kick state machine on its
50 Hz local control loop, so kick timing is unaffected by network jitter.

Usage:
    uv run src/python_client/teleop.py --host 192.168.1.123
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pygame

from roverc import RoverCClient
from widgets import Button, Slider

KEY_VX = 1.0
KEY_VY = 1.0
KEY_WZ = 1.0

# Motor index order matches the firmware's mecanum mapping (M1..M4).
TRIM_KEYS = ("front_left", "front_right", "rear_left", "rear_right")
TRIM_GRID_POS = {
    "front_left": (0, 0),
    "front_right": (1, 0),
    "rear_left": (0, 1),
    "rear_right": (1, 1),
}

WINDOW_SIZE = (820, 680)


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def push_config(client: RoverCClient, sliders: dict[str, Slider]) -> None:
    client.send_config(
        max_motor=int(sliders["max_motor"].value),
        kick_dur_ms=int(sliders["kick_dur_ms"].value),
        trim_fwd=[sliders[f"{k}.trim_fwd"].value for k in TRIM_KEYS],
        trim_bwd=[sliders[f"{k}.trim_bwd"].value for k in TRIM_KEYS],
        kick_fwd=[sliders[f"{k}.kick_fwd"].value for k in TRIM_KEYS],
        kick_bwd=[sliders[f"{k}.kick_bwd"].value for k in TRIM_KEYS],
    )


def build_layout(initial_max: int, initial_trim: list[float]) -> tuple[
    dict[str, Slider], dict[str, pygame.Rect], Button
]:
    margin = 16
    full_w = WINDOW_SIZE[0] - 2 * margin
    half_w = (full_w - 12) // 2
    sliders: dict[str, Slider] = {}

    header_bottom = 96
    globals_y = header_bottom + 16
    sliders["max_motor"] = Slider(
        pygame.Rect(margin, globals_y, half_w, 12),
        "max_motor", 0, 127, initial_max, step=1, fmt="{:.0f}",
    )
    sliders["kick_dur_ms"] = Slider(
        pygame.Rect(margin + half_w + 12, globals_y, half_w, 12),
        "kick_duration_ms", 0, 500, 100, step=10, fmt="{:.0f}",
    )

    grid_top = globals_y + 30
    grid_bottom = WINDOW_SIZE[1] - margin
    grid_height = grid_bottom - grid_top
    quad_w = (full_w - 12) // 2
    quad_h = (grid_height - 12) // 2
    quad_origin = {
        (0, 0): (margin, grid_top),
        (1, 0): (margin + quad_w + 12, grid_top),
        (0, 1): (margin, grid_top + quad_h + 12),
        (1, 1): (margin + quad_w + 12, grid_top + quad_h + 12),
    }
    quad_rects: dict[str, pygame.Rect] = {}

    for i, key in enumerate(TRIM_KEYS):
        col, row = TRIM_GRID_POS[key]
        qx, qy = quad_origin[(col, row)]
        quad_rects[key] = pygame.Rect(qx, qy, quad_w, quad_h)

        inner_x = qx + 12
        inner_top = qy + 36
        col_w = (quad_w - 36) // 2
        col_gap = 12
        row1_y = inner_top + 16
        row2_y = inner_top + 16 + 50
        seed = initial_trim[i]
        sliders[f"{key}.trim_fwd"] = Slider(
            pygame.Rect(inner_x, row1_y, col_w, 10), "fwd", 0.0, 2.0, seed, step=0.05,
        )
        sliders[f"{key}.trim_bwd"] = Slider(
            pygame.Rect(inner_x + col_w + col_gap, row1_y, col_w, 10), "bwd", 0.0, 2.0, seed, step=0.05,
        )
        sliders[f"{key}.kick_fwd"] = Slider(
            pygame.Rect(inner_x, row2_y, col_w, 10), "kick fwd", 0.0, 3.0, seed, step=0.05,
        )
        sliders[f"{key}.kick_bwd"] = Slider(
            pygame.Rect(inner_x + col_w + col_gap, row2_y, col_w, 10), "kick bwd", 0.0, 3.0, seed, step=0.05,
        )

    apply_btn = Button(pygame.Rect(WINDOW_SIZE[0] - 140, 12, 120, 32), "Apply")
    return sliders, quad_rects, apply_btn


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[2] / "config.json"),
        help="Path to config.json (default: repo-root/config.json)",
    )
    parser.add_argument("--host", default=None, help="Server IP shown on the StickC LCD.")
    parser.add_argument("--max-motor", type=int, default=None)
    parser.add_argument(
        "--trim",
        default=None,
        help='Initial steady trim "FL,FR,RL,RR" (applied to both directions and kicks).',
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    port = int(cfg["server"]["port"])
    rate_hz = int(cfg["control"]["rate_hz"])
    initial_max = (
        args.max_motor if args.max_motor is not None else int(cfg["control"]["max_motor"])
    )
    if not 0 <= initial_max <= 127:
        print(f"max_motor out of range [0, 127]: {initial_max}", file=sys.stderr)
        return 2

    if args.trim is not None:
        initial_trim = [float(x) for x in args.trim.split(",")]
    else:
        cfg_trim = cfg["control"].get("motor_trim", {})
        initial_trim = [float(cfg_trim.get(k, 1.0)) for k in TRIM_KEYS]
    if len(initial_trim) != 4 or any(not 0 <= v <= 4 for v in initial_trim):
        print(f"trim must be 4 values each in [0, 4]: {initial_trim}", file=sys.stderr)
        return 2

    host = args.host or input("server IP (from StickC LCD): ").strip()
    if not host:
        print("host is required", file=sys.stderr)
        return 2

    client = RoverCClient(host, port)

    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE)
    pygame.display.set_caption(f"RoverC teleop -> {host}:{port}")
    font = pygame.font.SysFont("monospace", 13)
    big_font = pygame.font.SysFont("monospace", 16)
    title_font = pygame.font.SysFont("monospace", 18, bold=True)
    clock = pygame.time.Clock()

    sliders, quad_rects, apply_btn = build_layout(initial_max, initial_trim)

    key_axis = {
        pygame.K_w: ("vx", +KEY_VX),
        pygame.K_s: ("vx", -KEY_VX),
        pygame.K_d: ("vy", +KEY_VY),
        pygame.K_a: ("vy", -KEY_VY),
        pygame.K_q: ("wz", +KEY_WZ),
        pygame.K_e: ("wz", -KEY_WZ),
    }

    pressed: set[int] = set()
    dirty = True
    last_apply_t = 0.0
    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    pressed.clear()
                elif event.key == pygame.K_RETURN:
                    push_config(client, sliders)
                    dirty = False
                    last_apply_t = time.time()
                elif event.key in key_axis:
                    pressed.add(event.key)
            elif event.type == pygame.KEYUP:
                pressed.discard(event.key)
            if apply_btn.handle_event(event):
                push_config(client, sliders)
                dirty = False
                last_apply_t = time.time()
            for s in sliders.values():
                if s.handle_event(event):
                    dirty = True

        vx = sum(amount for k, (axis, amount) in key_axis.items() if axis == "vx" and k in pressed)
        vy = sum(amount for k, (axis, amount) in key_axis.items() if axis == "vy" and k in pressed)
        wz = sum(amount for k, (axis, amount) in key_axis.items() if axis == "wz" and k in pressed)
        client.send_motion(vx, vy, wz)

        screen.fill((20, 20, 28))
        header = [
            f"target  : udp {host}:{port}    rate {rate_hz} Hz",
            f"vx={vx:+.2f}  vy={vy:+.2f}  wz={wz:+.2f}",
            "w/s vx  a/d vy  q/e wz  space stop  Esc quit  Enter=apply",
            f"applied  : {time.time() - last_apply_t:.1f}s ago" if last_apply_t else "applied  : (never)",
        ]
        for i, line in enumerate(header):
            screen.blit(big_font.render(line, True, (220, 220, 220)), (16, 12 + i * 20))

        for key in TRIM_KEYS:
            r = quad_rects[key]
            pygame.draw.rect(screen, (80, 80, 95), r, width=2, border_radius=6)
            screen.blit(title_font.render(key, True, (255, 220, 150)), (r.x + 12, r.y + 8))

        for s in sliders.values():
            s.draw(screen, font)
        apply_btn.draw(screen, big_font, accent=dirty)
        pygame.display.flip()
        clock.tick(rate_hz)

    client.send_stop()
    client.close()
    pygame.quit()
    print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
