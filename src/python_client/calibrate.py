#!/usr/bin/env python3
"""Automated calibration of the RoverC polynomial motor model via CMA-ES.

Drives the rover through random direction × release trials, scoring each
candidate polynomial set by integrated yaw residual during the driven and
release windows. The best coefficients are saved to a JSON file at every
generation; teleop.py loads that file at startup with `--coefs`.

Usage (run from repo root):
    uv run python src/python_client/calibrate.py \\
        --host 192.168.1.123 --generations 10 --pop-size 5 --out coefs/v1.json

Place the rover on a flat surface with enough slack space in every direction
that ~1.5s drives don't crash it; trials sample only forward/backward and
strafe (no commanded yaw), so the rover stays roughly centred. Telemetry is
turned on automatically via `cfg.tel = true`.
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

import cma  # type: ignore[import-not-found]
import numpy as np

from coefs import (
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

T_DRIVE = 1.5     # seconds commanded direction held
T_RELEASE = 1.5   # seconds after release where stop residual is measured
T_SETTLE = 0.5    # seconds extra at zero before next trial
TRIAL_TICK_S = 0.04  # 25 Hz, matches telemetry rate


def sample_direction(rng: random.Random) -> tuple[float, float, float]:
    """No commanded wz: cost function is `|gz|`, so non-zero wz_cmd would
    require a model-based reference. We can extend later once straight-line
    tracking is solved."""
    r = rng.random()
    sign = rng.choice([-1.0, +1.0])
    mag = rng.uniform(0.4, 1.0)
    if r < 0.7:
        return (sign * mag, 0.0, 0.0)
    return (0.0, sign * mag, 0.0)


def run_trial(
    client: RoverCClient,
    queue: TelemetryQueue,
    vx: float, vy: float, wz: float,
) -> list[tuple[float, TelemetryPacket]]:
    """Drive (vx, vy, wz) for T_DRIVE then release for T_RELEASE + T_SETTLE.
    Returns (relative_t_s, packet) pairs collected during the trial. Drains
    the queue first so cross-trial telemetry doesn't leak in."""
    queue.drain()
    t_start = time.monotonic()
    t_release = t_start + T_DRIVE
    t_end = t_release + T_RELEASE + T_SETTLE
    while time.monotonic() < t_end:
        elapsed = time.monotonic() - t_start
        if elapsed < T_DRIVE:
            client.send_motion(vx, vy, wz)
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
    """∫|gz| during drive + β·∫|gz| during release window.
    `beta > alpha` puts more weight on release residual (the visible
    artefact the user wants gone). Returns mean |gz| in deg/s, weighted."""
    if not pkts:
        return 1e6
    drive_sum = 0.0
    release_sum = 0.0
    n_drive = 0
    n_release = 0
    for t, p in pkts:
        if t < T_DRIVE:
            drive_sum += abs(p.gz_dps)
            n_drive += 1
        elif t < T_DRIVE + T_RELEASE:
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
    rng: random.Random,
    n_trials: int,
    log: logging.Logger,
) -> float:
    coefs = vector_to_coefs(cand_v, template)
    push_to_firmware(coefs, client.send_poly_chunk, client.send_config_dict)
    time.sleep(0.2)  # settle: let firmware finish applying chunks
    costs = []
    for ti in range(n_trials):
        vx, vy, wz = sample_direction(rng)
        pkts = run_trial(client, queue, vx, vy, wz)
        c = trial_cost(pkts)
        costs.append(c)
        log.debug("  trial %d/%d  (%.2f, %.2f, %.2f)  cost=%.3f  pkts=%d",
                  ti + 1, n_trials, vx, vy, wz, c, len(pkts))
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
    ap.add_argument("--pop-size", type=int, default=20)
    ap.add_argument("--sigma", type=float, default=0.05,
                    help="CMA-ES initial step. 0.05 keeps early candidates close to identity.")
    ap.add_argument("--n-trials", type=int, default=10)
    ap.add_argument("--pause-seconds", type=float, default=5.0,
                    help="Seconds to stop the rover between candidates so the "
                         "experimenter can recenter it within the test area. "
                         "0 disables.")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("calibrate")

    rng = random.Random(args.seed)

    template = load_json(args.init_coefs) if args.init_coefs else make_identity()

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
    es = cma.CMAEvolutionStrategy(
        x0=x0,
        sigma0=args.sigma,
        inopts={"popsize": args.pop_size, "verbose": -9},
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_json(template, args.out)
    log.info("seed coefficients saved to %s (pop=%d sigma=%.3f trials/cand=%d)",
             args.out, args.pop_size, args.sigma, args.n_trials)

    gen = 0
    candidates_done = 0
    try:
        while gen < args.generations:
            gen += 1
            t_gen_start = time.monotonic()
            cands = es.ask()
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
                    np.asarray(cand), template, client, queue, rng,
                    n_trials=args.n_trials, log=log,
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
