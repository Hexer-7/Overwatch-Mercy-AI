# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The text interface: ``Mercy AI.exe -cli``.

Everything the window does, in a console, with the arrow keys and Enter:
first-run setup, Start/Stop, Pause, live status and every setting.

The parts, from the outside in:

* :mod:`~mercy_ai.cli.console` opens the console and turns key records into
  key names and frames into screen updates.
* :mod:`~mercy_ai.cli.app` is the loop: read a key, ask the controller, carry
  out what it asks for through the bridge, draw.
* :mod:`~mercy_ai.cli.controller` decides what each key does. It is pure.
* :mod:`~mercy_ai.cli.screens` decides what each screen shows and draws it.
  It is pure too, and :mod:`~mercy_ai.cli.words` holds its sentences.
* :mod:`~mercy_ai.cli.capture` reads the next game key the player presses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

__all__ = ["run_cli"]


def run_cli(config_path: Path, console: Any, log_level: str = "WARNING") -> int:
    """Open the text interface on *console* and run it until the player quits."""
    from ..logging_setup import setup_logging
    from ..ui.session import open_session
    from .app import App, RingHandler

    # Nothing may write to the screen but the interface itself: log messages
    # are kept in memory instead (there is no log file).
    ring = RingHandler()
    setup_logging(log_level, handler=ring)
    root = logging.getLogger()
    root.addHandler(ring)
    logging.captureWarnings(True)
    try:
        from ..input_control import emergency_release

        if hasattr(console, "on_close"):
            # Closing the console window ends the process within seconds and
            # skips every finally block, so keys are let go of right there.
            console.on_close = emergency_release
        session = open_session(config_path)
        return App(console, session.bridge).run()
    finally:
        root.removeHandler(ring)
        logging.captureWarnings(False)
