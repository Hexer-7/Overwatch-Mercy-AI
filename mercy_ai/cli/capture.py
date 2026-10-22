# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Capturing the next game key the player presses.

``keyboard.hook(callback, suppress=True)`` runs the callback inside the
system-wide keyboard hook, before anything else sees the key. Returning
``False`` swallows it: the captured key never reaches the console (so it does
not also move the cursor), the game, or Mercy AI's own pause hook (so the pause
key can be bound without pausing). Only keys whose press was swallowed have
their release swallowed too, so the Enter that opened the capture is let go of
normally. Nothing is swallowed while another window is in front.

The control loop must be stopped first: its own key presses also pass through
the hook and must not be taken for the player's. The callback runs on the
``keyboard`` library's thread and must stay quick, and it never raises: an
exception there would silently stop every hook in the process. A capture that
nobody answers gives up after a while, because a hook that swallows keys must
never be left behind.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from .keys import scan_to_name

__all__ = ["CANCEL", "REFUSED", "KeyCapture", "keys_held_now"]

CANCEL = "cancel"
REFUSED = "refused"
ESCAPE_SCAN = 0x01


def keys_held_now() -> set[int]:
    """Scan codes of the keys that open a capture (Enter, Space), if held right now.

    The key that opened the capture may still be down when the hook starts,
    and its auto-repeat must not be taken for the player's answer.
    """
    if os.name != "nt":
        return set()
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32")
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        return {
            scan
            for vk, scan in ((0x0D, 0x1C), (0x20, 0x39))
            if user32.GetAsyncKeyState(vk) & 0x8000
        }
    except Exception:
        return set()


class KeyCapture:
    def __init__(
        self,
        hook: Callable[..., Any] | None = None,
        unhook: Callable[[Any], None] | None = None,
        is_foreground: Callable[[], bool] = lambda: True,
        timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hook, self._unhook = hook, unhook
        self._is_foreground = is_foreground
        self._timeout = timeout
        self._clock = clock
        self._handle: Any = None
        self._lock = threading.Lock()
        self._eaten: set[int] = set()
        self._captured: int | None = None
        self._result: str | None = None
        self._result_at = 0.0
        self._started = 0.0
        self._released = False
        self._held: set[int] = set()

    @property
    def active(self) -> bool:
        return self._handle is not None

    def start(self, held: set[int] | None = None) -> None:
        """Install the hook. Keys in *held* are let through until released."""
        if self._hook is None:  # pragma: no cover - needs Windows and the keyboard library
            import keyboard

            self._hook, self._unhook = keyboard.hook, keyboard.unhook
        self.rearm()
        self._held = set(held or ())
        self._handle = self._hook(self.on_event, suppress=True)

    def rearm(self) -> None:
        """Wait for another key, after the last one was refused."""
        with self._lock:
            self._result = None
            self._captured = None
            self._released = False
            self._started = self._clock()

    def stop(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None and self._unhook is not None:
            with contextlib.suppress(Exception):
                self._unhook(handle)

    def on_event(self, event: Any) -> bool:
        """The hook callback. ``True`` lets the key through; ``False`` swallows it."""
        try:
            scan = int(getattr(event, "scan_code", 0) or 0)
            down = getattr(event, "event_type", "down") == "down"
            with self._lock:
                if self._clock() - self._started > self._timeout:
                    return True  # a capture nobody answers must never keep swallowing keys
                if scan in self._held:
                    if not down:
                        self._held.discard(scan)
                    return True
                if not down:
                    if scan in self._eaten:
                        self._eaten.discard(scan)
                        if scan == self._captured:
                            self._released = True
                        return False
                    return True
                if not self._is_foreground():
                    return True
                self._eaten.add(scan)
                if self._result is None:
                    self._captured = scan
                    if scan == ESCAPE_SCAN:
                        self._result = CANCEL
                    else:
                        name = scan_to_name(scan, bool(getattr(event, "is_keypad", False)))
                        self._result = name or REFUSED
                    self._result_at = self._clock()
                return False
        except Exception:
            return True

    def poll(self) -> str | None:
        """``None`` while waiting; then a key name, :data:`CANCEL` or :data:`REFUSED`.

        Waits briefly for the key's release so that is swallowed too.
        """
        with self._lock:
            now = self._clock()
            if self._result is not None:
                if self._released or now - self._result_at > 0.6:
                    result, self._result = self._result, None
                    return result
                return None
            if now - self._started > self._timeout:
                return CANCEL
            return None
