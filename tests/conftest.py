# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Shared fixtures.

Nothing in this suite touches the game, the screen, or a real keyboard: every
test runs on synthetic arrays, temporary files, or fake objects.  That is the
whole point -- the vision maths, the config handling and the control logic are
verifiable offline, so the only thing left to check in-game is that the numbers
line up with what Overwatch actually draws.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RESOLUTIONS = ("2160", "1440", "1080", "768")


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return PROJECT_ROOT / "data"


@pytest.fixture
def rng() -> np.random.Generator:
    """Deterministic randomness -- a failing test must be reproducible."""
    return np.random.default_rng(20240201)


def make_frame(
    rng: np.random.Generator,
    height: int,
    width: int,
    *,
    channels: int = 4,
    low: int = 0,
    high: int = 256,
) -> np.ndarray:
    """A synthetic BGRA (or BGR) screen grab."""
    return rng.integers(low, high, (height, width, channels), dtype=np.uint8)


def make_bar_frame(height: int, width: int, fill: float, *, channels: int = 4) -> np.ndarray:
    """A crude 'health bar' filled to *fill* (0.0-1.0), white on black.

    Not meant to imitate Overwatch -- it just gives the pipeline a monotonic
    input so tests can assert that a fuller bar scores higher.
    """
    frame = np.zeros((height, width, channels), np.uint8)
    filled = round(width * fill)
    if filled:
        frame[:, :filled, :3] = 255
    if channels == 4:
        frame[:, :, 3] = 255
    return frame
