# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Key names for navigating the text interface, and for binding game keys.

Navigation keys arrive from the console as short strings: ``"up"``,
``"enter"``, ``"shift+tab"``, ``"ctrl+c"``, or the character itself for
printable keys (``"a"``, ``" "``, ``"5"``).

A game key is bound by its *physical* position, exactly as the window does it:
the scan code is turned into a browser ``KeyboardEvent.code`` and then through
:func:`mercy_ai.ui.model.normalise_key`, so the same key gets the same name in
both front ends whatever keyboard layout is active. (The ``keyboard`` library's
own ``event.name`` follows the layout: the P key reads as an Arabic letter on
an Arabic layout, which would not resolve later on another layout.)
"""

from __future__ import annotations

from ..ui.model import normalise_key

__all__ = ["CANCEL", "NAVIGATION", "SCAN_TO_CODE", "scan_to_name"]

#: Keys the interface itself understands, for documentation and tests.
NAVIGATION = frozenset(
    {
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "pgup",
        "pgdn",
        "enter",
        "esc",
        "tab",
        "shift+tab",
        "backspace",
        "delete",
        "ctrl+c",
        "resize",
        *(f"f{n}" for n in range(1, 13)),
    }
)

#: The key that cancels a key capture. It cannot be bound in the text
#: interface for that reason; the window can bind it.
CANCEL = "esc"

_LETTERS_ROW1 = "QWERTYUIOP"
_LETTERS_ROW2 = "ASDFGHJKL"
_LETTERS_ROW3 = "ZXCVBNM"

#: Set-1 scan code -> ``KeyboardEvent.code``.
SCAN_TO_CODE: dict[int, str] = {
    0x01: "Escape",
    **{0x02 + i: f"Digit{(i + 1) % 10}" for i in range(10)},
    0x0C: "Minus",
    0x0D: "Equal",
    0x0E: "Backspace",
    0x0F: "Tab",
    **{0x10 + i: f"Key{c}" for i, c in enumerate(_LETTERS_ROW1)},
    0x1A: "BracketLeft",
    0x1B: "BracketRight",
    0x1C: "Enter",
    0x1D: "ControlLeft",
    **{0x1E + i: f"Key{c}" for i, c in enumerate(_LETTERS_ROW2)},
    0x27: "Semicolon",
    0x28: "Quote",
    0x29: "Backquote",
    0x2A: "ShiftLeft",
    0x2B: "Backslash",
    **{0x2C + i: f"Key{c}" for i, c in enumerate(_LETTERS_ROW3)},
    0x33: "Comma",
    0x34: "Period",
    0x35: "Slash",
    0x36: "ShiftRight",
    0x38: "AltLeft",
    0x39: "Space",
    0x3A: "CapsLock",
    **{0x3B + i: f"F{i + 1}" for i in range(10)},
    0x47: "Home",
    0x48: "ArrowUp",
    0x49: "PageUp",
    0x4B: "ArrowLeft",
    0x4D: "ArrowRight",
    0x4F: "End",
    0x50: "ArrowDown",
    0x51: "PageDown",
    0x52: "Insert",
    0x53: "Delete",
    0x57: "F11",
    0x58: "F12",
    **{0x64 + i: f"F{13 + i}" for i in range(11)},
    0x76: "F24",
}


def scan_to_name(scan_code: int, is_keypad: bool = False) -> str | None:
    """The binding name for a physical key, or ``None`` if it cannot be bound.

    Keypad keys are refused, as the window refuses them: with Num Lock off the
    same key is an arrow, so the game may not see what Mercy AI presses.
    """
    if is_keypad:
        return None
    code = SCAN_TO_CODE.get(int(scan_code))
    if code is None:
        return None
    return normalise_key(code)
