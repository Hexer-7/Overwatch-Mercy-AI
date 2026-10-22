# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Shared run/pause state, read by the control loop and set from input threads.

Each flag is a ``threading.Event``: setting it from one thread and reading it
from another is well defined, and ``wait()`` lets the idle loop block with no
CPU cost and react the instant the trigger goes down instead of polling.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

__all__ = ["State"]


class State:
    """Whether the trigger is held, and whether the user has paused."""

    def __init__(self, on_change: Callable[[State], None] | None = None) -> None:
        self.run = threading.Event()
        self.paused = threading.Event()
        self._on_change = on_change
        #: Set by the loop when it wants the worker threads to stop.
        self.shutdown = threading.Event()

    # -- trigger ----------------------------------------------------------- #

    def trigger_pressed(self) -> None:
        self.run.set()

    def trigger_released(self) -> None:
        self.run.clear()

    # -- pause ------------------------------------------------------------- #

    def toggle_pause(self) -> bool:
        """Flip the pause flag and notify. Returns the new paused state.

        The caller's ``on_change`` is expected to release the held keys, so
        pausing while ``heal`` is down stops the beam immediately rather than on
        the loop's next iteration.
        """
        if self.paused.is_set():
            self.paused.clear()
        else:
            self.paused.set()
        if self._on_change is not None:
            self._on_change(self)
        return self.paused.is_set()

    @property
    def is_paused(self) -> bool:
        return self.paused.is_set()
