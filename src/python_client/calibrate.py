#!/usr/bin/env python3
"""Automated calibration of the RoverC polynomial motor model via CMA-ES.

Drives the rover through cycled direction × release trials, scoring each
candidate polynomial set by integrated yaw residual during the driven and
release windows. The best coefficients are saved to a JSON file at every
generation; teleop.py loads that file at startup with `--coefs`.

Usage (run from repo root):
    uv run python src/python_client/calibrate.py \\
        --host 192.168.1.123 --generations 10 --pop-size 5 --out coefs/v1.json

Each trial drives one of four fixed patterns (cardinal + diagonals, no
commanded yaw) through the cycle drive(+) → release → drive(-) → release.
The two release windows isolate stop-residual yaw cleanly, and the
symmetric leg pair keeps net displacement per trial near zero so the
rover stays on the desk across repeated trials. Telemetry is turned on
automatically via `cfg.tel = true`.
"""
from __future__ import annotations

import argparse
import logging
import math
import random
import sys
import time
from pathlib import Path

import cma  # type: ignore[import-not-found]
import numpy as np

from coefs import (
    DEFAULT_POLY_ORDER,
    POLY_MAX_ORDER,
    CoefSet,
    coefs_to_vector,
    load_json,
    make_identity,
    push_to_firmware,
    save_json,
    vector_to_coefs,
)
from roverc import RoverCClient
from telemetry import TelemetryPacket, TelemetryQueue

T_DRIVE = 1.0     # seconds each driven leg (forward, then reverse)
T_RELEASE = 1.0   # seconds release after each driven leg
TRIAL_TICK_S = 0.04  # 25 Hz, matches telemetry rate

# A trial covers four phases back-to-back: drive(+), release, drive(-),
# release. Each release window is where stop residual yaw is scored, and
# the forward / reverse legs are isolated (no instantaneous reversal) so
# the rover doesn't pitch from the abrupt sign flip.
T_FWD_END = T_DRIVE
T_REL1_END = T_FWD_END + T_RELEASE
T_REV_END = T_REL1_END + T_DRIVE
T_REL2_END = T_REV_END + T_RELEASE

# Fixed direction patterns cycled by trial index. Each trial drives the
# pattern then its negation, so the four patterns cover 8 directions
# total. Diagonals are scaled by 1/sqrt(2) so per-axis amplitude matches
# the cardinal cases.
_D = 1.0 / math.sqrt(2.0)
DIRECTION_PATTERNS: list[tuple[float, float]] = [
    (1.0, 0.0),    # forward / backward
    (0.0, 1.0),    # right / left strafe
    (_D, _D),      # forward-right / backward-left diagonal
    (_D, -_D),     # forward-left / backward-right diagonal
]


def make_trial_plan(rng: random.Random, n_trials: int) -> list[tuple[float, float, float]]:
    """Return n_trials commanded `(vx, vy, wz)` tuples. Pattern order is
    block-shuffled so every direction pattern appears equally often per
    lap, and magnitudes are sampled in `[0.4, 0.7]` so the polynomial
    sees a range of `s` values (low-speed coefficients are otherwise
    never excited) while per-leg coast distance stays bounded. wz=0
    throughout: cost is `|gz|`, so non-zero wz_cmd would require a
    model-based reference. Generated once per generation and shared
    across candidates so cost differences reflect coefficient
    differences, not trial luck."""
    pattern_order: list[int] = []
    while len(pattern_order) < n_trials:
        block = list(range(len(DIRECTION_PATTERNS)))
        rng.shuffle(block)
        pattern_order.extend(block)
    pattern_order = pattern_order[:n_trials]
    plan: list[tuple[float, float, float]] = []
    for idx in pattern_order:
        dx, dy = DIRECTION_PATTERNS[idx]
        mag = rng.uniform(0.4, 0.7)
        plan.append((mag * dx, mag * dy, 0.0))
    return plan


def run_trial(
    client: RoverCClient,
    queue: TelemetryQueue,
    vx: float, vy: float, wz: float,
) -> list[tuple[float, TelemetryPacket]]:
    """Run one trial: drive (vx, vy, wz) for T_DRIVE, release for
    T_RELEASE, drive (-vx, -vy, -wz) for T_DRIVE, release for T_RELEASE.
    The two release windows give clean stop-residual measurements (no
    sign flip mid-drive that would induce pitching). Net displacement
    stays bounded because the legs are symmetric. Drains the queue first
    so cross-trial telemetry doesn't leak in."""
    queue.drain()
    t_start = time.monotonic()
    t_end = t_start + T_REL2_END
    while time.monotonic() < t_end:
        elapsed = time.monotonic() - t_start
        if elapsed < T_FWD_END:
            client.send_motion(vx, vy, wz)
        elif elapsed < T_REL1_END:
            client.send_motion(0.0, 0.0, 0.0)
        elif elapsed < T_REV_END:
            client.send_motion(-vx, -vy, -wz)
        else:
            client.send_motion(0.0, 0.0, 0.0)
        time.sleep(TRIAL_TICK_S)
    pkts = queue.drain()
    return [(p.pc_t - t_start, p) for p in pkts]


def trial_cost(
    pkts: list[tuple[float, TelemetryPacket]],
    alpha: float = 1.0,
    beta: float = 2.0,
) -> float:
    """α·mean|gz| over both drive legs + β·mean|gz| over both release
    windows. `beta > alpha` puts more weight on the release residual
    (the visible artefact the user wants gone). Returns weighted mean
    |gz| in deg/s."""
    if not pkts:
        return 1e6
    drive_sum = 0.0
    release_sum = 0.0
    n_drive = 0
    n_release = 0
    for t, p in pkts:
        if t < T_FWD_END:
            drive_sum += abs(p.gz_dps)
            n_drive += 1
        elif t < T_REL1_END:
            release_sum += abs(p.gz_dps)
            n_release += 1
        elif t < T_REV_END:
            drive_sum += abs(p.gz_dps)
            n_drive += 1
        elif t < T_REL2_END:
            release_sum += abs(p.gz_dps)
            n_release += 1
    if n_drive == 0:
        return 1e6
    drive_avg = drive_sum / n_drive
    release_avg = release_sum / n_release if n_release else drive_avg
    return alpha * drive_avg + beta * release_avg


def evaluate_candidate(
    cand_v: np.ndarray,
    template: CoefSet,
    client: RoverCClient,
    queue: TelemetryQueue,
    trial_plan: list[tuple[float, float, float]],
    log: logging.Logger,
) -> float:
    coefs = vector_to_coefs(cand_v, template)
    push_to_firmware(coefs, client.send_poly_chunk, client.send_config_dict)
    time.sleep(0.2)  # settle: let firmware finish applying chunks
    costs = []
    for ti, (vx, vy, wz) in enumerate(trial_plan):
        pkts = run_trial(client, queue, vx, vy, wz)
        c = trial_cost(pkts)
        costs.append(c)
        log.debug("  trial %d/%d  (%.2f, %.2f, %.2f)  cost=%.3f  pkts=%d",
                  ti + 1, len(trial_plan), vx, vy, wz, c, len(pkts))
    return float(sum(costs) / len(costs))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", required=True, help="StickC IP shown on the LCD.")
    ap.add_argument("--port", type=int, default=4210)
    ap.add_argument("--generations", type=int, required=True,
                    help="Number of CMA-ES generations to run. Stop the run "
                         "with Ctrl-C if you want to bail early; the latest "
                         "best is already in --out.")
    ap.add_argument("--out", type=Path, required=True,
                    help="JSON path where the running best coefficient set is saved.")
    ap.add_argument("--init-coefs", type=Path, default=None,
                    help="Seed CMA-ES from this JSON instead of identity defaults.")
    ap.add_argument("--poly-order", type=int, default=DEFAULT_POLY_ORDER,
                    help=f"Polynomial degree for f_k(t) and f_b(t). Default "
                         f"{DEFAULT_POLY_ORDER}, max {POLY_MAX_ORDER}. Ignored "
                         "if --init-coefs is provided (the JSON's poly_order "
                         "wins to keep CMA-ES dimensions consistent).")
    ap.add_argument("--pop-size", type=int, default=None,
                    help="CMA-ES population size lambda. Defaults to Hansen's "
                         "heuristic 4 + floor(3 * ln(n)) where n is the "
                         "coefficient vector dimension.")
    ap.add_argument("--sigma", type=float, default=0.05,
                    help="CMA-ES initial step. 0.05 keeps early candidates close to identity.")
    ap.add_argument("--n-trials", type=int, default=8,
                    help="Trials per candidate. Defaults to 8 = 4 direction "
                         "patterns x 2 cycles.")
    ap.add_argument("--pause-seconds", type=float, default=15.0,
                    help="Seconds to stop the rover between candidates so the "
                         "experimenter can recenter it within the test area. "
                         "0 disables.")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    if args.out.exists():
        ap.error(
            f"--out path already exists: {args.out}. Refusing to overwrite a "
            "previous calibration result. Pick a new path, or pass the existing "
            "file via --init-coefs and write to a fresh --out."
        )
    if not (1 <= args.poly_order <= POLY_MAX_ORDER):
        ap.error(f"--poly-order must be in [1, {POLY_MAX_ORDER}]")

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("calibrate")

    rng = random.Random(args.seed)

    template = (
        load_json(args.init_coefs)
        if args.init_coefs
        else make_identity(poly_order=args.poly_order)
    )

    queue = TelemetryQueue(maxlen=20000)
    client = RoverCClient(args.host, args.port, on_telemetry=queue.on_packet)

    # Register sender (StickC pushes telemetry to last UDP src), enable telemetry,
    # then push the seed coefficient set so the firmware starts in a known state.
    client.send_motion(0.0, 0.0, 0.0)
    time.sleep(0.05)
    client.send_config_dict({"tel": True})
    time.sleep(0.1)
    push_to_firmware(template, client.send_poly_chunk, client.send_config_dict)
    time.sleep(0.4)

    if len(queue) == 0:
        log.warning("no telemetry packets received yet -- continuing, but the cost "
                    "function will be useless until packets arrive.")
    else:
        log.info("telemetry warm-up: %d packets buffered", len(queue))
    queue.drain()

    x0 = coefs_to_vector(template)
    pop_size = args.pop_size if args.pop_size is not None else 4 + int(3 * math.log(len(x0)))
    es = cma.CMAEvolutionStrategy(
        x0=x0,
        sigma0=args.sigma,
        inopts={"popsize": pop_size, "verbose": -9},
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_json(template, args.out)
    log.info("seed coefficients saved to %s (pop=%d sigma=%.3f trials/cand=%d)",
             args.out, pop_size, args.sigma, args.n_trials)

    gen = 0
    candidates_done = 0
    try:
        while gen < args.generations:
            gen += 1
            t_gen_start = time.monotonic()
            cands = es.ask()
            # Same trial sequence (pattern order + magnitudes) for every
            # candidate in this generation, so cost differences reflect
            # coefficient differences rather than trial luck. Re-rolled
            # each generation.
            trial_plan = make_trial_plan(rng, args.n_trials)
            fits: list[float] = []
            for ci, cand in enumerate(cands):
                if candidates_done > 0 and args.pause_seconds > 0:
                    # Between candidates: stop the rover and idle so the
                    # experimenter can recenter it. Telemetry collected during
                    # the pause is dropped so the next trial cost isn't
                    # contaminated by stationary samples. Re-send the stop
                    # every 0.1 s so firmware failsafe (200 ms timeout)
                    # doesn't kick the rover into an unknown state.
                    log.info("pausing %.1fs to recenter rover (gen %d cand %d/%d)",
                             args.pause_seconds, gen, ci + 1, len(cands))
                    pause_end = time.monotonic() + args.pause_seconds
                    while time.monotonic() < pause_end:
                        client.send_motion(0.0, 0.0, 0.0)
                        time.sleep(0.1)
                    queue.drain()
                fit = evaluate_candidate(
                    np.asarray(cand), template, client, queue,
                    trial_plan=trial_plan, log=log,
                )
                fits.append(fit)
                candidates_done += 1
                log.info("gen %d cand %d/%d  cost=%.3f deg/s",
                         gen, ci + 1, len(cands), fit)
            es.tell(cands, fits)
            best_v = es.result.xbest
            if best_v is None:
                best_v = cands[int(np.argmin(fits))]
            best_coefs = vector_to_coefs(np.asarray(best_v), template)
            save_json(best_coefs, args.out)
            log.info("gen %d  min=%.3f  saved %s  (gen %.1fs)",
                     gen, min(fits), args.out, time.monotonic() - t_gen_start)
    except KeyboardInterrupt:
        log.info("interrupted by user; saving final state")
    finally:
        client.send_motion(0.0, 0.0, 0.0)
        client.send_config_dict({"tel": False})
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
