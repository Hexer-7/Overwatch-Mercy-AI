# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Everything both faces of the program need before they show anything.

The window and the text interface open the same way: look at the machine, read
``config.xml`` (or decide that first-run setup is needed), load the templates
for the configured resolution, and build the :class:`LoopRunner` and the
:class:`Bridge` that every later action goes through. Doing it here, with no
window involved, is what keeps the two from drifting apart.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, NamedTuple

from ..config import Config
from ..errors import MercyAIError
from ..paths import CONFIG_PATH, RESOLUTIONS
from .bridge import Bridge
from .runner import LoopRunner

log = logging.getLogger(__name__)

__all__ = ["Session", "StartingConfig", "build_environment", "open_session", "starting_config"]


class StartingConfig(NamedTuple):
    config: Config
    needs_setup: bool
    error: str | None


class Session(NamedTuple):
    bridge: Bridge
    runner: LoopRunner
    config_path: Path


def build_environment() -> dict[str, Any]:
    """Facts about this machine that the program shows but cannot work out itself.

    Every probe is wrapped: a diagnostic that fails must degrade to "unknown",
    never stop the program it is describing.
    """
    from ..winutil import detect_screen_height, is_elevated, list_monitors, primary_monitor

    environment: dict[str, Any] = {
        "needs_setup": False,
        "elevated": None,
        "detected": None,
        "primary": None,
        "resolutions": list(RESOLUTIONS),
    }
    try:
        environment["elevated"] = is_elevated()
    except Exception:
        log.debug("could not determine elevation", exc_info=True)
    try:
        monitors = list_monitors()
        primary = primary_monitor(monitors)
        environment["primary"] = f"{primary['width']}x{primary['height']}"
        environment["detected"] = detect_screen_height(monitors)
    except Exception:
        log.debug("could not enumerate monitors", exc_info=True)
    return environment


def starting_config(config_path: Path, detected: str | None) -> StartingConfig:
    """Read ``config.xml``, or make a blank one for first-run setup to fill in."""
    from ..config import read_config

    error = None
    if config_path.is_file():
        try:
            return StartingConfig(read_config(config_path), False, None)
        except MercyAIError as exc:
            # A broken config is not a reason to refuse to open: setup is the
            # one place the player can fix it without editing XML by hand. Setup
            # writes a fresh file, so the player is told why.
            log.warning("config.xml could not be read (%s); starting setup", exc)
            error = str(exc)
    # No keys are suggested: the player picks their own in setup.
    blank = Config(resolution=detected or "1440", heal_btn="", damage_boost_btn="")
    return StartingConfig(blank, True, error)


def open_session(config_path: Path | None = None) -> Session:
    """Read the settings, load the templates and build the bridge."""
    config_path = config_path or CONFIG_PATH
    environment = build_environment()
    config, needs_setup, config_error = starting_config(config_path, environment["detected"])
    environment["needs_setup"] = needs_setup
    environment["config_error"] = config_error

    assets = None
    if not needs_setup:
        try:
            from ..assets import load_assets

            assets = load_assets(config.resolution)
        except MercyAIError as exc:
            # Missing templates are recoverable by picking another resolution
            # in Settings, so the program still opens and says so.
            log.warning("assets for %s could not be loaded: %s", config.resolution, exc)
            environment["asset_error"] = str(exc)

    runner = LoopRunner(config, assets)
    bridge = Bridge(
        config=config,
        config_path=config_path,
        assets=assets,
        runner=runner,
        environment=environment,
    )
    return Session(bridge, runner, config_path)
