# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Logging.

Mercy AI writes no log file: nothing is left behind next to the program, or
anywhere else. Messages go to stderr, or to a handler the caller supplies; the
windowed build has no console, so by default it never shows them.
"""

from __future__ import annotations

import logging

__all__ = ["setup_logging"]

_LOGGER_NAME = "mercy_ai"


def setup_logging(level: str = "WARNING", handler: logging.Handler | None = None) -> logging.Logger:
    """Send Mercy AI's messages to *handler* (stderr by default), at *level* and above."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.WARNING))
    logger.handlers.clear()
    logger.propagate = False

    if handler is None:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    return logger
