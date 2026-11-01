# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Is the scoreboard key held?  One ``GetAsyncKeyState`` call a frame.

Holding the scoreboard open covers the health ring, and a covered ring reads
like a fall.  :class:`~mercy_ai.flash_heal.FlashHealPlanner` already tells the
two apart by the shape of the fall, but not always; knowing the key is down
settles it.  So while it is held Flash Heal decides nothing, and the moment it
comes up the health is compared with just before it went down.

Why ``GetAsyncKeyState`` and not a keyboard hook
------------------------------------------------
The loop asks once a frame and needs only "down or up right now", which is
exactly what ``user32.GetAsyncKeyState`` answers: a read of the key state the
system already keeps, well under a microsecond, with no thread, no hook and no
event queue.  A hook would add a callback on every key press in the system for
the sake of one key.

The key is turned into a virtual-key code once, when the loop is built.  Names
the ``keyboard`` library uses for the common keys map straight to their codes;
anything else goes through its scan code and ``MapVirtualKeyW``, the same scan
code :class:`~mercy_ai.input_control.KeyController` presses keys by.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .errors import ConfigError

log = logging.getLogger(__name__)

__all__ = ["ScoreboardKey", "virtual_key"]

#: The high bit of ``GetAsyncKeyState``: the key is down right now.
_DOWN = 0x8000

#: ``MapVirtualKeyW`` mode: scan code to virtual-key code, telling left and
#: right apart.
_MAPVK_VSC_TO_VK_EX = 3

#: ``keyboard``-library names -> virtual-key codes, for keys with no letter.
_NAMED: dict[str, int] = {
    "tab": 0x09,
    "backspace": 0x08,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "caps lock": 0x14,
    "esc": 0x1B,
    "space": 0x20,
    "page up": 0x21,
    "page down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
}


def _user32() -> Any:
    """A private ``user32`` handle with the two signatures this module calls.

    Private, as in :func:`mercy_ai.cli.capture.keys_held_now`, so setting
    ``restype`` here cannot change ``ctypes.windll.user32`` for anyone else.
    """
    import ctypes

    user32 = ctypes.WinDLL("user32")
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.MapVirtualKeyW.restype = ctypes.c_uint
    user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
    return user32


def virtual_key(
    name: str,
    scan_codes: Callable[[str], tuple[int, ...]] | None = None,
    map_scan: Callable[[int, int], int] | None = None,
) -> int:
    """The virtual-key code ``GetAsyncKeyState`` wants for *name*.

    *scan_codes* and *map_scan* replace ``keyboard.key_to_scan_codes`` and
    ``user32.MapVirtualKeyW`` for the keys that need them, so this runs in
    tests anywhere.
    """
    key = (name or "").strip().lower()
    if key in _NAMED:
        return _NAMED[key]
    if len(key) == 1 and ("a" <= key <= "z" or "0" <= key <= "9"):
        return ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x6F + int(key[1:])  # VK_F1 is 0x70

    if scan_codes is None:
        import keyboard

        scan_codes = keyboard.key_to_scan_codes
    if map_scan is None:
        user32 = _user32()
        map_scan = user32.MapVirtualKeyW
    try:
        codes = scan_codes(key)
    except (ValueError, KeyError) as exc:
        raise ConfigError(f"The scoreboard key {name!r} is not a key Mercy AI knows.") from exc
    for code in codes:
        vk = int(map_scan(code, _MAPVK_VSC_TO_VK_EX))
        if vk:
            return vk
    raise ConfigError(f"The scoreboard key {name!r} is not a key Mercy AI knows.")


class ScoreboardKey:
    """Answers whether the scoreboard key is down, once a frame."""

    def __init__(self, name: str, poll: Callable[[int], int] | None = None) -> None:
        self.name = name
        self.vk = virtual_key(name)
        if poll is None:
            poll = _user32().GetAsyncKeyState
        self._poll = poll
        log.info("scoreboard key: %s (vk 0x%02X)", name, self.vk)

    def held(self) -> bool:
        return bool(self._poll(self.vk) & _DOWN)
