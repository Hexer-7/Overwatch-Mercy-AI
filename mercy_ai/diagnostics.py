# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Calibration, benchmarking and region dumps, run from the command line.

They answer the two common support questions: "what number should I put in
``mercy_bar_percentage``?" and "what is Mercy AI actually looking at?".
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import imageops
from .assets import Assets
from .capture import CombinedCapture, ScreenGrabber, captureblt_enabled, use_fast_capture
from .config import Config, wants_boost
from .vision import GAUSS_KSIZE, prepare_binary, score_region, white_mask
from .weapon import WeaponReading, read_weapon

log = logging.getLogger(__name__)

__all__ = [
    "benchmark",
    "calibrate",
    "compare_capture_modes",
    "dump_region",
    "render_calibration_line",
]


def render_calibration_line(
    health: float,
    bomb: float,
    config: Config,
    boosting: bool = False,
    weapon: WeaponReading | None = None,
) -> str:
    """One line of the calibration readout.

    Split out from the loop so its formatting is testable without a screen.
    """
    boost = wants_boost(
        health,
        config.threshold,
        boosting,
        config.hysteresis,
        config.dynamic_heal_drop if config.dynamic_heal else 0.0,
    ) or (config.bomb_detection and bomb >= config.bomb_threshold)
    action = "BOOST" if boost else "HEAL "
    filled = max(0, min(40, round(health * 40)))
    bar = "#" * filled
    line = (
        f"health={health:6.3f}  bomb={bomb:6.3f}  {action}  "
        f"|{bar:<40}|  threshold={config.threshold:.3f}"
    )
    if weapon is not None:
        seen = weapon.describe(config.flash_heal_weapon_threshold)
        flash = "R allowed" if seen == "staff" else "R held"
        line += f"  weapon={seen:<7} {flash}"
    return line


def calibrate(
    assets: Assets,
    config: Config,
    grabber: ScreenGrabber | None = None,
    iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    echo: Callable[[str], None] = print,
    update: Callable[[str], None] | None = None,
) -> None:
    """Live score readout, so the threshold can be tuned outside a match.

    The user points at a full-health and a damaged ally and reads the score off
    directly, instead of guessing ``mercy_bar_percentage`` in a live match.
    """
    echo("Calibration. Point at an ally and watch the score. Ctrl+C to stop.\n")
    update = update or _redraw
    owns_grabber = grabber is None
    grabber = grabber or ScreenGrabber()
    capture = CombinedCapture(assets.geometry.health, assets.geometry.bomb, grabber)
    count = 0
    try:
        while iterations is None or count < iterations:
            count += 1
            health_frame, bomb_frame = capture.frames()
            health = score_region(health_frame, assets.health, assets.health_buffers)
            bomb = score_region(bomb_frame, assets.bomb, assets.bomb_buffers)
            # Grabbed last: the grab reuses the buffer both frames above point into.
            weapon = (
                None
                if assets.weapon is None
                else read_weapon(grabber.grab(assets.weapon.region.as_mss()), assets.weapon)
            )
            update(render_calibration_line(health, bomb, config, weapon=weapon))
            sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if owns_grabber:
            grabber.close()
    echo(
        "\n\nMove 'Switch to damage boost at' in Settings so the score crosses the switch "
        f"point where you want it. The switch point now is {config.threshold:.3f} "
        f"(dial at {config.threshold_percent:g})."
    )


def _redraw(line: str) -> None:
    """Rewrite the current console line in place."""
    sys.stdout.write("\r" + line + "\033[K")
    sys.stdout.flush()


def benchmark(
    assets: Assets,
    grabber: ScreenGrabber | None = None,
    seconds: float = 3.0,
    clock: Callable[[], float] = time.perf_counter,
    echo: Callable[[str], None] = print,
) -> dict[str, float]:
    """Time each stage of the pipeline.

    Lets anyone measure performance on their own machine, and makes a
    regression visible.
    """
    owns_grabber = grabber is None
    grabber = grabber or ScreenGrabber()
    capture = CombinedCapture(assets.geometry.health, assets.geometry.bomb, grabber)
    stages: dict[str, float] = {"capture": 0.0, "health": 0.0, "bomb": 0.0}
    frames = 0
    try:
        deadline = clock() + seconds
        while clock() < deadline:
            start = clock()
            health_frame, bomb_frame = capture.frames()
            mid = clock()
            score_region(health_frame, assets.health, assets.health_buffers)
            after_health = clock()
            score_region(bomb_frame, assets.bomb, assets.bomb_buffers)
            end = clock()

            stages["capture"] += mid - start
            stages["health"] += after_health - mid
            stages["bomb"] += end - after_health
            frames += 1
    finally:
        if owns_grabber:
            grabber.close()

    total = sum(stages.values())
    if frames and total > 0:
        mode = {True: "on", False: "off", None: "n/a"}[captureblt_enabled()]
        echo(
            f"\n{frames} frames in {total:.3f}s -> {frames / total:8.1f} fps   (CAPTUREBLT {mode})"
        )
        for name, elapsed in sorted(stages.items(), key=lambda kv: -kv[1]):
            echo(f"  {name:10s} {elapsed * 1e6 / frames:9.1f} us/frame  ({elapsed / total:5.1%})")
    stages["frames"] = float(frames)
    stages["total"] = total
    return stages


def compare_capture_modes(
    assets: Assets,
    grabber: ScreenGrabber | None = None,
    seconds: float = 1.0,
    clock: Callable[[], float] = time.perf_counter,
    echo: Callable[[str], None] = print,
) -> dict[str, float]:
    """Time the screen grab with and without CAPTUREBLT.

    The flag is the single largest per-frame cost on a composited desktop, and
    how large depends on the machine -- so measure it here rather than assert it.
    """
    if captureblt_enabled() is None:
        echo("Capture-mode comparison is Windows-only; skipping.")
        return {}

    owns = grabber is None
    grabber = grabber or ScreenGrabber()
    capture = CombinedCapture(assets.geometry.health, assets.geometry.bomb, grabber)
    results: dict[str, float] = {}
    try:
        for label, fast in (("CAPTUREBLT on (mss default)", False), ("CAPTUREBLT off", True)):
            use_fast_capture(fast)
            for _ in range(5):
                capture.frames()
            frames = 0
            start = clock()
            deadline = start + seconds
            while clock() < deadline:
                capture.frames()
                frames += 1
            elapsed = clock() - start
            per_frame = elapsed / frames * 1e6 if frames else float("nan")
            results[label] = per_frame
            echo(f"  {label:<28} {per_frame:9.1f} us/grab   ({frames / elapsed:7.1f} grabs/s)")
    finally:
        use_fast_capture(True)
        if owns:
            grabber.close()

    if len(results) == 2:
        with_flag = results["CAPTUREBLT on (mss default)"]
        without_flag = results["CAPTUREBLT off"]
        if without_flag > 0:
            echo(f"\n  Turning CAPTUREBLT off is {with_flag / without_flag:.1f}x faster here.")
    return results


def dump_region(
    assets: Assets,
    out: Path,
    grabber: ScreenGrabber | None = None,
    frame: np.ndarray | None = None,
) -> list[Path]:
    """Save the raw grab plus every pipeline stage, to show what Mercy AI sees.

    Each image is written beside *out* as ``<out stem>.<stage>.png``.  ``frame``
    can be supplied directly, so this also runs without a screen.
    """
    owns_grabber = grabber is None and frame is None
    try:
        if frame is None:
            grabber = grabber or ScreenGrabber()
            frame = grabber.grab(assets.geometry.health.as_mss())

        written: list[Path] = []

        def save(suffix: str, image: np.ndarray) -> None:
            path = out.with_name(out.stem + suffix + ".png")
            imageops.imwrite(path, image)
            written.append(path)

        save(".raw", frame)
        mask = white_mask(frame)
        save(".mask", mask)
        if assets.health.fast_path and assets.health.keep_mask is not None:
            masked = imageops.bitwise_and(mask, assets.health.keep_mask)
            save(".masked", masked)
            save(".blurred", imageops.gaussian_blur(masked, GAUSS_KSIZE))
            save(".stage_final", prepare_binary(frame, assets.health))
        return written
    finally:
        if owns_grabber and grabber is not None:
            grabber.close()
