# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Measuring, cutting and wrapping text for a fixed-size character grid.

Strings may carry ANSI colour codes, which take no space on screen, and any
Unicode text (a player's folder name can be Arabic), so widths are counted in
terminal cells rather than with ``len()``.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["cell_width", "char_width", "fit", "strip_ansi", "truncate", "wrap"]

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TOKEN = re.compile(r"(\x1b\[[0-9;?]*[A-Za-z])")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def char_width(char: str) -> int:
    if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 2
    return 1


def cell_width(text: str) -> int:
    """How many terminal cells *text* takes, ignoring colour codes."""
    return sum(char_width(char) for char in strip_ansi(text))


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    """Cut *text* to at most *width* cells, keeping colour codes intact."""
    if width <= 0:
        return ""
    if cell_width(text) <= width:
        return text
    room = width - cell_width(ellipsis)
    out: list[str] = []
    used = 0
    for token in _TOKEN.split(text):
        if not token:
            continue
        if _ANSI.fullmatch(token):
            out.append(token)
            continue
        for char in token:
            w = char_width(char)
            if used + w > room:
                break
            out.append(char)
            used += w
        else:
            continue
        break
    if room >= 0:
        out.append(ellipsis if cell_width(ellipsis) <= width else "")
    return "".join(out)


def fit(text: str, width: int, ellipsis: str = "…") -> str:
    """Exactly *width* cells: cut if longer, padded with spaces if shorter.

    A line that carries colour codes ends with a reset, so a style cut off
    mid-way never runs on into whatever is drawn next to it.
    """
    text = truncate(text, width, ellipsis)
    if "\x1b[" in text:
        text += "\x1b[0m"
    return text + " " * max(0, width - cell_width(text))


def wrap(text: str, width: int) -> list[str]:
    """Word-wrap plain *text* to *width* cells, keeping its own line breaks."""
    width = max(1, width)
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        line = ""
        for word in words:
            while cell_width(word) > width:
                # A word longer than the line (a path, a URL) is broken hard.
                room = width - cell_width(line) - (1 if line else 0)
                if room <= 0:
                    lines.append(line)
                    line = ""
                    continue
                head, word = _split_at(word, room)
                lines.append(f"{line} {head}" if line else head)
                line = ""
            candidate = f"{line} {word}" if line else word
            if cell_width(candidate) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def _split_at(word: str, cells: int) -> tuple[str, str]:
    used = 0
    for index, char in enumerate(word):
        used += char_width(char)
        if used > cells:
            return word[:index], word[index:]
    return word, ""
