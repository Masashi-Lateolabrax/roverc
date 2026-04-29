#!/usr/bin/env python3
"""Keyboard teleop for RoverC via the StickC Plus2 UDP server.

Three pygame windows (SDL2 multi-window):
  - Input: capture WASD/QE keystrokes; only active when this window has focus
  - Settings: per-wheel trim / kick sliders + globals; mouse-driven
  - Cameras: left/right JPEG streams shown side-by-side

Usage:
    uv run src/python_client/teleop.py --host 192.168.1.123
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import pygame
from pygame._sdl2.video import Renderer, Texture, Window

from camera import (
    FRAMESIZE_CHOICES,
    CameraInfo,
    CameraRegistry,
    CameraStream,
    set_camera_params,
)
from coefs import load_json as load_coefs_json
from coefs import push_to_firmware as push_coefs
from roverc import RoverCClient
from telemetry import TelemetryPacket, parse as parse_telemetry
from widgets import Button, ChoiceRow, Slider

import threading

KEY_VX = 1.0
KEY_VY = 1.0
KEY_WZ = 1.0

TRIM_KEYS = ("front_left", "front_right", "rear_left", "rear_right")
TRIM_GRID_POS = {
    "front_left": (0, 0),
    "front_right": (1, 0),
    "rear_left": (0, 1),
    "rear_right": (1, 1),
}

INPUT_SIZE = (600, 320)
SETTINGS_SIZE = (820, 600)
CAMERA_VIEW_SIZE = (320, 240)
CAMERA_SIZE = (CAMERA_VIEW_SIZE[0] * 2 + 48, CAMERA_VIEW_SIZE[1] + 96)
CAMERA_ROLES = ("left", "right")

G_TO_MPS2 = 9.80665
# Maximum estimated speed used to scale the est-velocity vector to half the HUD.
EST_VEL_FULL_MPS = 0.5
# Body-axis mapping from MPU6886 axes. Flip signs after empirical check on the
# rover (push forward, observe whether vx_est goes positive). 0 = IMU x, 1 = y,
# 2 = z. Forward positive = +x, left positive = +y.
IMU_FWD_AXIS = 0
IMU_FWD_SIGN = +1.0
IMU_LEFT_AXIS = 1
IMU_LEFT_SIGN = +1.0


class VelocityEstimator:
    """Body-frame velocity estimator from MPU6886 accel telemetry.

    Strategy:
      - During idle (no key pressed) the gravity vector in body frame is
        learned via slow EMA, and the integrated velocity is held at zero.
      - During driven motion the gravity baseline is held, accel is corrected
        by `a_corr = (a_raw - g_baseline) * G_TO_MPS2`, and v is integrated
        with the trapezoidal rule using StickC fw_t_ms as the timebase.
      - Velocity resets to zero on every idle re-entry. This bounds drift to
        the duration of a single press.
    """

    GRAVITY_EMA = 0.02

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._g = (0.0, 0.0, 0.0)
        self._g_init = False
        self._last_t_ms: int | None = None
        self._vx = 0.0
        self._vy = 0.0
        self._idle = True

    def set_idle(self, idle: bool) -> None:
        with self._lock:
            if idle and not self._idle:
                self._vx = 0.0
                self._vy = 0.0
            self._idle = idle

    def on_packet(self, pkt: TelemetryPacket) -> None:
        a = (pkt.ax_g, pkt.ay_g, pkt.az_g)
        with self._lock:
            if not self._g_init:
                self._g = a
                self._g_init = True
            elif self._idle:
                e = self.GRAVITY_EMA
                self._g = (
                    (1 - e) * self._g[0] + e * a[0],
                    (1 - e) * self._g[1] + e * a[1],
                    (1 - e) * self._g[2] + e * a[2],
                )

            t_ms = pkt.fw_t_ms
            if self._last_t_ms is None:
                self._last_t_ms = t_ms
                return
            dt_ms = (t_ms - self._last_t_ms) & 0xFFFFFFFF
            self._last_t_ms = t_ms
            if dt_ms == 0 or dt_ms > 1000:
                return
            dt = dt_ms / 1000.0
            if not self._idle:
                a_fwd = (a[IMU_FWD_AXIS] - self._g[IMU_FWD_AXIS]) * IMU_FWD_SIGN * G_TO_MPS2
                a_left = (a[IMU_LEFT_AXIS] - self._g[IMU_LEFT_AXIS]) * IMU_LEFT_SIGN * G_TO_MPS2
                self._vx += a_fwd * dt
                self._vy += a_left * dt

    def snapshot(self) -> tuple[float, float, bool]:
        with self._lock:
            return (self._vx, self._vy, self._g_init)


class WindowPanel:
    """Pairs an SDL2 Window+Renderer with an offscreen Surface for drawing."""

    def __init__(self, title: str, size: tuple[int, int], position=None) -> None:
        if position is not None:
            self.window = Window(title, size=size, position=position)
        else:
            self.window = Window(title, size=size)
        self.renderer = Renderer(self.window)
        self.size = size
        self.surface = pygame.Surface(size)

    @property
    def id(self) -> int:
        return self.window.id

    def present(self) -> None:
        tex = Texture.from_surface(self.renderer, self.surface)
        self.renderer.clear()
        tex.draw()
        self.renderer.present()


def event_window_id(event: pygame.event.Event) -> int | None:
    win = getattr(event, "window", None)
    return getattr(win, "id", None) if win is not None else None


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


def build_settings_layout(initial_max: int, initial_trim: list[float]) -> tuple[
    dict[str, Slider], dict[str, pygame.Rect], Button, ChoiceRow, Slider
]:
    margin = 16
    full_w = SETTINGS_SIZE[0] - 2 * margin
    half_w = (full_w - 12) // 2
    sliders: dict[str, Slider] = {}

    apply_btn = Button(pygame.Rect(SETTINGS_SIZE[0] - 140, 12, 120, 32), "Apply")

    globals_y = 64
    sliders["max_motor"] = Slider(
        pygame.Rect(margin, globals_y, half_w, 12),
        "max_motor", 0, 127, initial_max, step=1, fmt="{:.0f}",
    )
    sliders["kick_dur_ms"] = Slider(
        pygame.Rect(margin + half_w + 12, globals_y, half_w, 12),
        "kick_duration_ms", 0, 500, 100, step=10, fmt="{:.0f}",
    )

    cam_y = globals_y + 36
    cam_row_h = 28
    cam_choice_w = half_w
    qvga_index = next(
        (i for i, (name, _, _) in enumerate(FRAMESIZE_CHOICES) if name == "QVGA"),
        0,
    )
    framesize_choice = ChoiceRow(
        pygame.Rect(margin, cam_y, cam_choice_w, cam_row_h),
        "camera framesize",
        [name for name, _, _ in FRAMESIZE_CHOICES],
        selected_index=qvga_index,  # QVGA, matches firmware default
    )
    quality_slider = Slider(
        pygame.Rect(margin + cam_choice_w + 12, cam_y + 8, half_w, 12),
        "jpeg_quality", 4, 63, 30, step=1, fmt="{:.0f}",
    )

    grid_top = cam_y + cam_row_h + 16
    grid_bottom = SETTINGS_SIZE[1] - margin
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

    return sliders, quad_rects, apply_btn, framesize_choice, quality_slider


def draw_velocity_hud(
    surface: pygame.Surface, origin: tuple[int, int], size: tuple[int, int],
    vx: float, vy: float, wz: float,
    vx_est: float, vy_est: float, est_ready: bool,
    font: pygame.font.Font,
) -> None:
    ox, oy = origin
    w, h = size
    cx = ox + w // 2
    cy = oy + h // 2

    # Frame
    pygame.draw.rect(surface, (40, 40, 52), pygame.Rect(ox, oy, w, h), border_radius=8)
    pygame.draw.rect(surface, (80, 80, 95), pygame.Rect(ox, oy, w, h), width=2, border_radius=8)

    # Cross axes
    pygame.draw.line(surface, (70, 70, 80), (ox + 10, cy), (ox + w - 10, cy), 1)
    pygame.draw.line(surface, (70, 70, 80), (cx, oy + 10, ), (cx, oy + h - 10), 1)

    half = min(w, h) // 2 - 14

    # Estimated body-frame velocity vector (orange). vx_est forward = up,
    # vy_est left = left on screen.
    est_color = (220, 140, 70) if est_ready else (110, 80, 60)
    sx = max(-1.0, min(1.0, vx_est / EST_VEL_FULL_MPS))
    sy = max(-1.0, min(1.0, vy_est / EST_VEL_FULL_MPS))
    est_tip_x = cx - int(sy * half)
    est_tip_y = cy - int(sx * half)
    pygame.draw.line(surface, est_color, (cx, cy), (est_tip_x, est_tip_y), 3)
    pygame.draw.circle(surface, est_color, (est_tip_x, est_tip_y), 5)

    # Commanded vector tip (vx forward = up, green). Drawn on top so the
    # operator's input always wins the foreground.
    tip_x = cx + int(vy * half)
    tip_y = cy - int(vx * half)
    pygame.draw.line(surface, (90, 200, 140), (cx, cy), (tip_x, tip_y), 4)
    pygame.draw.circle(surface, (240, 240, 240), (tip_x, tip_y), 6)

    # wz arc indicator
    if abs(wz) > 1e-3:
        arc_rect = pygame.Rect(cx - 22, cy - 22, 44, 44)
        if wz > 0:
            pygame.draw.arc(surface, (220, 160, 60), arc_rect, 0, 1.6 * wz, 3)
        else:
            pygame.draw.arc(surface, (220, 160, 60), arc_rect, 1.6 * wz + 6.28, 6.28, 3)

    # Numeric readout below
    cmd_text = f"cmd vx={vx:+.2f}  vy={vy:+.2f}  wz={wz:+.2f}"
    if est_ready:
        est_text = f"est vx={vx_est:+.2f}  vy={vy_est:+.2f} m/s"
    else:
        est_text = "est ...waiting for telemetry..."
    surface.blit(font.render(cmd_text, True, (220, 220, 220)), (ox + 8, oy + h + 6))
    surface.blit(font.render(est_text, True, est_color), (ox + 8, oy + h + 22))


def render_input(
    panel: WindowPanel, host: str, port: int, rate_hz: int,
    pressed: set[int], vx: float, vy: float, wz: float,
    vx_est: float, vy_est: float, est_ready: bool,
    last_apply_t: float, dirty: bool, focused: bool,
    apply_btn: Button, big_font: pygame.font.Font, font: pygame.font.Font,
) -> None:
    s = panel.surface
    s.fill((20, 20, 28))

    header = [
        f"target  : udp {host}:{port}    rate {rate_hz} Hz",
        "WASD vx/vy   QE wz   Space stop   Enter apply   Esc quit",
        f"applied : {time.time() - last_apply_t:4.1f}s ago" if last_apply_t else "applied : (never)",
    ]
    color = (220, 220, 220) if focused else (150, 150, 160)
    for i, line in enumerate(header):
        s.blit(big_font.render(line, True, color), (16, 12 + i * 20))

    focus_label = "[focused]" if focused else "[click to focus]"
    s.blit(font.render(focus_label, True, (200, 220, 140) if focused else (200, 120, 60)),
           (INPUT_SIZE[0] - 140, 14))

    draw_velocity_hud(
        s, (16, 100), (200, 160), vx, vy, wz, vx_est, vy_est, est_ready, font,
    )

    # Pressed-key chips
    chips_x = 240
    chips_y = 100
    chip_w = 40
    chip_h = 28
    layout = {
        "W": (1, 0), "A": (0, 1), "S": (1, 1), "D": (2, 1),
        "Q": (3, 0), "E": (4, 0),
    }
    key_const = {
        "W": pygame.K_w, "A": pygame.K_a, "S": pygame.K_s, "D": pygame.K_d,
        "Q": pygame.K_q, "E": pygame.K_e,
    }
    for label, (col, row) in layout.items():
        rx = chips_x + col * (chip_w + 6)
        ry = chips_y + row * (chip_h + 6)
        active = key_const[label] in pressed
        bg = (90, 170, 110) if active else (50, 50, 60)
        pygame.draw.rect(s, bg, pygame.Rect(rx, ry, chip_w, chip_h), border_radius=4)
        txt = big_font.render(label, True, (240, 240, 240))
        s.blit(txt, txt.get_rect(center=(rx + chip_w // 2, ry + chip_h // 2)))

    apply_btn.draw(s, big_font, accent=dirty)


def render_settings(
    panel: WindowPanel, sliders: dict[str, Slider], quad_rects: dict[str, pygame.Rect],
    apply_btn: Button, dirty: bool,
    framesize_choice: ChoiceRow, quality_slider: Slider, cam_status: str,
    title_font: pygame.font.Font, big_font: pygame.font.Font, font: pygame.font.Font,
) -> None:
    s = panel.surface
    s.fill((20, 20, 28))

    s.blit(title_font.render("settings", True, (220, 220, 220)), (16, 14))
    s.blit(font.render("(mouse-only; keyboard input goes to the input window)", True, (160, 160, 170)),
           (16, 40))

    for key in TRIM_KEYS:
        r = quad_rects[key]
        pygame.draw.rect(s, (80, 80, 95), r, width=2, border_radius=6)
        s.blit(title_font.render(key, True, (255, 220, 150)), (r.x + 12, r.y + 8))

    for sl in sliders.values():
        sl.draw(s, font)
    apply_btn.draw(s, big_font, accent=dirty)

    framesize_choice.draw(s, font)
    quality_slider.draw(s, font)
    if cam_status:
        s.blit(
            font.render(cam_status, True, (180, 180, 195)),
            (16, framesize_choice.rect.bottom + 6),
        )


def render_cameras(
    panel: WindowPanel,
    title_font: pygame.font.Font, info_font: pygame.font.Font,
    big_font: pygame.font.Font,
    registry: CameraRegistry,
    stream_states: dict[str, dict],
) -> None:
    s = panel.surface
    s.fill((20, 20, 28))

    panel_w = (CAMERA_SIZE[0] - 48) // 2
    margin = 16
    y = 16
    panel_h = CAMERA_SIZE[1] - 32
    panels = {
        "left":  pygame.Rect(margin, y, panel_w, panel_h),
        "right": pygame.Rect(margin + panel_w + 16, y, panel_w, panel_h),
    }

    for role in CAMERA_ROLES:
        prect = panels[role]
        pygame.draw.rect(s, (80, 80, 95), prect, width=2, border_radius=6)
        s.blit(title_font.render(f"camera ({role})", True, (255, 220, 150)),
               (prect.x + 12, prect.y + 8))

        info = registry.latest(role)
        image_origin = (prect.x + 10, prect.y + 36)
        view_rect = pygame.Rect(image_origin, CAMERA_VIEW_SIZE)
        pygame.draw.rect(s, (10, 10, 14), view_rect)

        status_lines: list[str] = []
        if info is None:
            status_lines.append(f"waiting for {role} (StickC I2C probe)...")
        else:
            age_s = time.monotonic() - info.last_seen_monotonic
            status_lines.append(f"http: {info.ip}:{info.http_port}{info.jpg_path}")
            status_lines.append(f"seen: {age_s:.1f}s ago  cam_ok={info.camera_ok}")

        state = stream_states[role]
        stream: CameraStream | None = state.get("stream")
        surface: pygame.Surface | None = state.get("surface")
        last_frame_count: int = state.get("last_frame_count", 0)
        recent_ts: list[float] = state.get("recent_ts", [])
        freeze_events: list[tuple[float, float]] = state.get("freeze_events", [])
        fps_1s = 0.0
        err_count = 0
        max_gap_30s = 0.0
        freezes_30s = 0
        current_gap_s = 0.0
        FREEZE_THRESHOLD = 1.0
        WINDOW_S = 30.0

        if stream is not None:
            jpeg, frame_count, last_fetch_t, last_error, err_count = stream.latest()
            now = time.monotonic()
            if jpeg is not None and frame_count != last_frame_count:
                try:
                    decoded = pygame.image.load(io.BytesIO(jpeg))
                    if decoded.get_size() != CAMERA_VIEW_SIZE:
                        decoded = pygame.transform.smoothscale(decoded, CAMERA_VIEW_SIZE)
                    surface = decoded
                    state["surface"] = surface
                    if recent_ts:
                        gap = now - recent_ts[-1]
                        if gap >= FREEZE_THRESHOLD:
                            freeze_events.append((now, gap))
                    recent_ts.append(now)
                    state["last_frame_count"] = frame_count
                except (pygame.error, ValueError) as exc:
                    status_lines.append(f"decode error: {exc}")

            cutoff = now - WINDOW_S
            recent_ts = [t for t in recent_ts if t >= cutoff]
            freeze_events = [(t, g) for (t, g) in freeze_events if t >= cutoff]
            state["recent_ts"] = recent_ts
            state["freeze_events"] = freeze_events

            cutoff_1s = now - 1.0
            fps_1s = float(sum(1 for t in recent_ts if t >= cutoff_1s))

            if recent_ts:
                gaps = [recent_ts[i] - recent_ts[i - 1] for i in range(1, len(recent_ts))]
                current_gap_s = now - recent_ts[-1]
                max_gap_30s = max(gaps + [current_gap_s], default=0.0)
            else:
                max_gap_30s = 0.0
            freezes_30s = len(freeze_events)

            if last_fetch_t is not None:
                stale = now - last_fetch_t
                status_lines.append(f"fetch: {stale*1000:.0f}ms ago")
            else:
                status_lines.append("fetch: pending")
            status_lines.append(
                f"30s: max_gap={max_gap_30s:.1f}s  freezes(>1s)={freezes_30s}"
            )
            if last_error is not None:
                status_lines.append(f"err : {last_error[:40]}")

        if surface is not None:
            s.blit(surface, image_origin)

        # Big top-right HUD: live freeze indicator (red when current gap >0.5s)
        # plus rolling 30s freeze stats. Designed to make "did 8MHz help?"
        # answerable from numbers alone.
        if current_gap_s >= 0.5:
            live_color = (220, 90, 90)
            live_text = f"FROZEN {current_gap_s:.1f}s"
        else:
            live_color = (220, 220, 220)
            live_text = f"{fps_1s:>2.0f} fps"
        live_surf = big_font.render(live_text, True, live_color)
        s.blit(live_surf, (prect.right - live_surf.get_width() - 12, prect.y + 8))

        stats_text = f"30s: {freezes_30s} freezes  max {max_gap_30s:.1f}s  err {err_count}"
        stats_color = (220, 90, 90) if freezes_30s > 0 else (180, 200, 180)
        stats_surf = info_font.render(stats_text, True, stats_color)
        s.blit(stats_surf, (prect.right - stats_surf.get_width() - 12, prect.y + 28))

        text_y = view_rect.bottom + 10
        for line in status_lines:
            s.blit(info_font.render(line, True, (200, 200, 210)),
                   (prect.x + 12, text_y))
            text_y += 16


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
    parser.add_argument(
        "--coefs",
        type=Path,
        default=None,
        help="Polynomial coefficient JSON to push to the firmware at startup. "
             "Produced by calibrate.py or scripts/make_identity_coefs.py.",
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

    estimator = VelocityEstimator()

    def _on_telemetry(raw: bytes) -> None:
        pkt = parse_telemetry(raw)
        if pkt is not None:
            estimator.on_packet(pkt)

    camera_registry = CameraRegistry()
    client = RoverCClient(
        host, port, camera_registry=camera_registry, on_telemetry=_on_telemetry,
    )

    # Register sender (StickC pushes telemetry to the last UDP src) and turn
    # telemetry on so the velocity estimator gets fed.
    client.send_motion(0.0, 0.0, 0.0)
    time.sleep(0.05)
    client.send_config_dict({"tel": True})

    if args.coefs is not None:
        # Blast the saved polynomial table. Manual slider tweaks below still
        # write the constant a[0][0] term of STEADY/KICK cells via the JSON
        # cfg path.
        coefs = load_coefs_json(args.coefs)
        n_sent = push_coefs(coefs, client.send_poly_chunk, client.send_config_dict)
        print(f"pushed {n_sent} polynomial chunks from {args.coefs}")

    pygame.init()
    pygame.display.init()

    input_panel = WindowPanel(
        f"RoverC input -> {host}:{port}", INPUT_SIZE, position=(80, 80),
    )
    settings_panel = WindowPanel(
        "RoverC settings", SETTINGS_SIZE, position=(80 + INPUT_SIZE[0] + 16, 80),
    )
    camera_panel = WindowPanel(
        "RoverC cameras", CAMERA_SIZE, position=(80, 80 + INPUT_SIZE[1] + 40),
    )

    font = pygame.font.SysFont("monospace", 13)
    big_font = pygame.font.SysFont("monospace", 16)
    title_font = pygame.font.SysFont("monospace", 18, bold=True)
    clock = pygame.time.Clock()

    (
        sliders, quad_rects, settings_apply_btn,
        framesize_choice, quality_slider,
    ) = build_settings_layout(initial_max, initial_trim)
    input_apply_btn = Button(
        pygame.Rect(INPUT_SIZE[0] - 132, INPUT_SIZE[1] - 50, 116, 36), "Apply",
    )

    stream_states: dict[str, dict] = {
        role: {
            "stream": None,
            "surface": None,
            "last_frame_count": 0,
            # Frame arrival timestamps within the last 30s window.
            "recent_ts": [],
            # (time, gap_s) for inter-frame gaps >= FREEZE_THRESHOLD in the
            # last 30s. Used for the freeze count / max-gap HUD.
            "freeze_events": [],
        }
        for role in CAMERA_ROLES
    }

    key_axis = {
        pygame.K_w: ("vx", +KEY_VX),
        pygame.K_s: ("vx", -KEY_VX),
        pygame.K_d: ("vy", +KEY_VY),
        pygame.K_a: ("vy", -KEY_VY),
        pygame.K_q: ("wz", -KEY_WZ),
        pygame.K_e: ("wz", +KEY_WZ),
    }

    pressed: set[int] = set()
    dirty = True
    last_apply_t = 0.0
    cam_status = "(not pushed yet)"
    focused_id: int | None = input_panel.id
    running = True

    def do_apply() -> None:
        nonlocal dirty, last_apply_t, cam_status
        push_config(client, sliders)

        # Camera /control: the firmware's sync WebServer cannot serve /control
        # while a /stream client is connected, so stop active streams first
        # and let the auto-start loop reopen /stream on the next render tick.
        idx = framesize_choice.selected_index
        fs_name, fs_value, fs_dim = FRAMESIZE_CHOICES[idx]
        q = int(quality_slider.value)
        for role in CAMERA_ROLES:
            state = stream_states[role]
            stream: CameraStream | None = state.get("stream")
            if stream is not None:
                stream.stop()
                stream.join(timeout=2.0)
                state["stream"] = None
        # Brief pause so the camera firmware exits handle_stream and main
        # loop re-accepts before we hit /control.
        time.sleep(0.1)
        results: list[str] = []
        for role in CAMERA_ROLES:
            info: CameraInfo | None = camera_registry.latest(role)
            if info is None or not info.camera_ok:
                results.append(f"{role}:none")
                continue
            ok, body = set_camera_params(info, framesize=fs_value, quality=q)
            tag = "ok" if ok else "err"
            results.append(f"{role}:{tag}")
            print(f"camera {role} /control fs={fs_value} q={q} -> {tag} | {body}")
        cam_status = (
            f"cam fs={fs_name}({fs_dim[0]}x{fs_dim[1]}) q={q}  "
            + " ".join(results)
        )

        dirty = False
        last_apply_t = time.time()

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                continue
            if event.type == pygame.WINDOWCLOSE:
                running = False
                continue
            if event.type == pygame.WINDOWFOCUSGAINED:
                focused_id = event_window_id(event)
                continue
            if event.type == pygame.WINDOWFOCUSLOST:
                wid = event_window_id(event)
                if wid == input_panel.id:
                    pressed.clear()
                continue

            wid = event_window_id(event)

            if event.type == pygame.KEYDOWN and wid == input_panel.id:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    pressed.clear()
                elif event.key == pygame.K_RETURN:
                    do_apply()
                elif event.key in key_axis:
                    pressed.add(event.key)
                continue
            if event.type == pygame.KEYUP and wid == input_panel.id:
                pressed.discard(event.key)
                continue

            # Mouse: route to widgets in whichever window the cursor is over.
            if wid == settings_panel.id:
                if settings_apply_btn.handle_event(event):
                    do_apply()
                for sl in sliders.values():
                    if sl.handle_event(event):
                        dirty = True
                if framesize_choice.handle_event(event):
                    dirty = True
                if quality_slider.handle_event(event):
                    dirty = True
            elif wid == input_panel.id:
                if input_apply_btn.handle_event(event):
                    do_apply()

        vx = sum(amount for k, (axis, amount) in key_axis.items() if axis == "vx" and k in pressed)
        vy = sum(amount for k, (axis, amount) in key_axis.items() if axis == "vy" and k in pressed)
        wz = sum(amount for k, (axis, amount) in key_axis.items() if axis == "wz" and k in pressed)
        client.send_motion(vx, vy, wz)

        estimator.set_idle(not pressed)
        vx_est, vy_est, est_ready = estimator.snapshot()

        # Auto-start camera streams when StickC reports them.
        for role in CAMERA_ROLES:
            state = stream_states[role]
            if state["stream"] is None:
                info = camera_registry.latest(role)
                if info is not None and info.camera_ok:
                    stream = CameraStream(info)
                    stream.start()
                    state["stream"] = stream
                    print(f"camera discovered ({role}): {info.ip}:{info.http_port}")

        render_input(
            input_panel, host, port, rate_hz, pressed, vx, vy, wz,
            vx_est, vy_est, est_ready,
            last_apply_t, dirty, focused_id == input_panel.id,
            input_apply_btn, big_font, font,
        )
        render_settings(
            settings_panel, sliders, quad_rects, settings_apply_btn, dirty,
            framesize_choice, quality_slider, cam_status,
            title_font, big_font, font,
        )
        render_cameras(camera_panel, title_font, font, big_font, camera_registry, stream_states)

        input_panel.present()
        settings_panel.present()
        camera_panel.present()

        clock.tick(rate_hz)

    client.send_stop()
    client.close()
    for state in stream_states.values():
        if state["stream"] is not None:
            state["stream"].stop()
    pygame.quit()
    print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
