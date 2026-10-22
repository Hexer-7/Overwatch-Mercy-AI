# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Is Overwatch the foreground window, and did a click land on it?

Two checks that work together:

* :func:`click_is_on_overwatch` runs once, when the mouse trigger goes down,
  and asks which window is under the click.  Windows delivers the press
  *before* it moves focus, so at that instant the foreground window is still
  the game even when the click is on another window or monitor -- only the
  window under the pointer tells the truth.
* :class:`FocusGate` runs every frame while the trigger is held and asks which
  window is in front, which catches an alt-tab, a minimised game or a key
  trigger, none of which a click check can see.

Both look up the window's process and compare the image name, with the name
memoised per pid.

Win32 imports are lazy so this module can be imported and tested anywhere; the
lookup functions are injectable for the same reason.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import lru_cache

log = logging.getLogger(__name__)

__all__ = [
    "FOCUS_TTL",
    "OVERWATCH_PROCESS",
    "PID_CACHE_TTL",
    "FocusGate",
    "click_is_on_overwatch",
    "is_overwatch_focused",
    "process_name_for_hwnd",
    "process_name_for_pid",
]

OVERWATCH_PROCESS = "Overwatch.exe"

#: A human alt-tab takes far longer than this, so the gate stays responsive.
FOCUS_TTL = 0.10

#: Windows recycles pids, so the name cache is flushed periodically.  A stale
#: entry can only affect the optional focus gate, never the control decision.
PID_CACHE_TTL = 60.0


@lru_cache(maxsize=64)
def _cached_process_name(pid: int) -> str | None:
    import psutil

    try:
        return psutil.Process(pid).name()
    except Exception:
        # psutil.NoSuchProcess / AccessDenied, both expected and both meaning
        # "not something we can identify".
        return None


def process_name_for_pid(pid: int) -> str | None:
    """Image name for *pid*, memoised."""
    return _cached_process_name(pid)


def clear_pid_cache() -> None:
    _cached_process_name.cache_clear()


def process_name_for_hwnd(hwnd: int) -> str | None:
    """Image name of the process owning *hwnd*."""
    import win32process

    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return process_name_for_pid(pid)


def _foreground_hwnd() -> int:
    import win32gui

    return win32gui.GetForegroundWindow()


def is_overwatch_focused(
    foreground: Callable[[], int] | None = None,
    name_for_hwnd: Callable[[int], str | None] | None = None,
) -> bool:
    """True if the foreground window belongs to Overwatch.

    This **fails open**: any error (a transient ``psutil.AccessDenied`` is
    common) counts as focused.  The focus gate is an optional convenience, and
    a broken *gate* must never disable the *tool*.
    """
    foreground = foreground or _foreground_hwnd
    name_for_hwnd = name_for_hwnd or process_name_for_hwnd
    try:
        hwnd = foreground()
        if not hwnd:
            return False
        return name_for_hwnd(hwnd) == OVERWATCH_PROCESS
    except Exception:
        log.debug("focus check failed; assuming focused", exc_info=True)
        return True


def _root_window_at(x: int, y: int) -> int:
    import win32gui

    GA_ROOT = 2
    return win32gui.GetAncestor(win32gui.WindowFromPoint((x, y)), GA_ROOT)


def click_is_on_overwatch(
    x: int,
    y: int,
    window_at: Callable[[int, int], int] | None = None,
    name_for_hwnd: Callable[[int], str | None] | None = None,
) -> bool:
    """True if the screen point *x*, *y* is on an Overwatch window.

    Physical pixels, as the mouse hook reports them; the process is DPI aware,
    so this holds across monitors with different scaling.  A minimised game is
    never under the pointer, so it needs no check of its own.

    **Fails open**, like :func:`is_overwatch_focused`: a check that cannot run
    must not stop the tool.
    """
    window_at = window_at or _root_window_at
    name_for_hwnd = name_for_hwnd or process_name_for_hwnd
    try:
        hwnd = window_at(x, y)
        if not hwnd:
            return False
        return name_for_hwnd(hwnd) == OVERWATCH_PROCESS
    except Exception:
        log.debug("click check failed; assuming it is on the game", exc_info=True)
        return True


class FocusGate:
    """Caches the focus answer for a short TTL.

    Even at two syscalls, the answer cannot meaningfully change between two
    frames a few milliseconds apart.
    """

    def __init__(
        self,
        ttl: float = FOCUS_TTL,
        check: Callable[[], bool] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ttl = ttl
        self._check = check or is_overwatch_focused
        self._clock = clock or time.perf_counter
        self._stamp: float | None = None
        self._value = False
        self._pid_cache_stamp = self._clock()

    def is_focused(self) -> bool:
        now = self._clock()
        if self._stamp is not None and now - self._stamp < self.ttl:
            return self._value
        if now - self._pid_cache_stamp >= PID_CACHE_TTL:
            clear_pid_cache()
            self._pid_cache_stamp = now
        self._value = self._check()
        self._stamp = now
        return self._value
