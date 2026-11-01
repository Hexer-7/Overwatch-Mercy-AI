# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Colours and glyphs for the text interface.

The accent is the window's wine, as 24-bit colour, each with the nearest of
the 16 standard colours in front of it for a console that has no 24-bit
colour (a 24-bit code then overrides it; the legacy console ignores it). A
background is used only where the text brings its own (the Advanced notice is
wine on white): a console's own background can be anything. Focus is shown
with reverse video. Colour is never the only
signal; every state also has a word or a symbol, so ``NO_COLOR`` loses nothing.

Every glyph is in Consolas and Lucida Console, the default console fonts, so
the console draws them without font fallback. :data:`ASCII` replaces them for a
console that cannot show them.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ASCII", "UNICODE", "Glyphs", "Style"]


@dataclass(frozen=True)
class Glyphs:
    rule: str
    vbar: str
    top_left: str
    top_right: str
    bottom_left: str
    bottom_right: str
    fill: str
    track: str
    tick: str
    up: str
    down: str
    knob: str
    radio_on: str
    radio_off: str
    dot_on: str
    dot_off: str
    on: str
    off: str
    tick_box: str
    empty_box: str
    focus: str
    left: str
    right: str
    arrows_lr: str
    arrows_ud: str
    dash: str
    middot: str
    ellipsis: str
    path: str


UNICODE = Glyphs(
    rule="─",
    vbar="│",
    top_left="┌",
    top_right="┐",
    bottom_left="└",
    bottom_right="┘",
    fill="█",
    track="░",
    tick="│",
    up="▲",
    down="▼",
    knob="●",
    radio_on="(●)",
    radio_off="( )",
    dot_on="●",
    dot_off="○",
    on="■ On",
    off="□ Off",
    tick_box="[x]",
    empty_box="[ ]",
    focus="›",
    left="‹",
    right="›",
    arrows_lr="←→",
    arrows_ud="↑↓",
    dash="—",
    middot="·",
    ellipsis="…",
    path="→",
)

ASCII = Glyphs(
    rule="-",
    vbar="|",
    top_left="+",
    top_right="+",
    bottom_left="+",
    bottom_right="+",
    fill="#",
    track=".",
    tick="|",
    up="^",
    down="v",
    knob="O",
    radio_on="(*)",
    radio_off="( )",
    dot_on="O",
    dot_off="o",
    on="[x] On",
    off="[ ] Off",
    tick_box="[x]",
    empty_box="[ ]",
    focus=">",
    left="<",
    right=">",
    arrows_lr="Left/Right",
    arrows_ud="Up/Down",
    dash="-",
    middot="-",
    ellipsis="...",
    path=">",
)


#: Wine for fills (the bar, the knob) and a lighter step of it for text, which
#: the darker one is too dim to carry on a dark console. Same as the window.
WINE = (35, 38, 2, 138, 28, 60)
WINE_TEXT = (35, 38, 2, 218, 108, 140)


def _sgr(*codes: int) -> str:
    return "\x1b[" + ";".join(str(code) for code in codes) + "m"


RESET = "\x1b[0m"


@dataclass(frozen=True)
class Style:
    """How text is coloured: *color* off keeps only bold and reverse."""

    color: bool = True
    glyphs: Glyphs = UNICODE

    def _paint(self, text: str, *codes: int) -> str:
        if not text:
            return text
        if not self.color:
            codes = tuple(code for code in codes if code in (1, 7))
        if not codes:
            return text
        return _sgr(*codes) + text + RESET

    def dim(self, text: str) -> str:
        return self._paint(text, 90)

    def bold(self, text: str) -> str:
        return self._paint(text, 1)

    def accent(self, text: str) -> str:
        return self._paint(text, *WINE_TEXT)

    def accent_bold(self, text: str) -> str:
        return self._paint(text, 1, *WINE_TEXT)

    def fill(self, text: str) -> str:
        """A solid block in the accent, like the window's health bar."""
        return self._paint(text, *WINE)

    def good(self, text: str) -> str:
        return self._paint(text, 32)

    def good_bold(self, text: str) -> str:
        return self._paint(text, 1, 32)

    def warn(self, text: str) -> str:
        return self._paint(text, 33)

    def warn_bold(self, text: str) -> str:
        return self._paint(text, 1, 33)

    def error(self, text: str) -> str:
        return self._paint(text, 91)

    def error_bold(self, text: str) -> str:
        return self._paint(text, 1, 91)

    def notice(self, text: str) -> str:
        """Wine on white, for the one line that must not be missed."""
        return self._paint(text, *WINE, 107)

    def claude(self, text: str) -> str:
        """Claude's own orange. A console without 24-bit colour keeps the
        dark yellow that comes first, the nearest of the standard 16."""
        return self._paint(text, 33, 38, 2, 217, 119, 87)

    def reverse(self, text: str) -> str:
        return self._paint(text, 7)

    def badge(self, text: str, tone: str) -> str:
        """A state word in reverse video, coloured by *tone*."""
        colour = {"good": 32, "warn": 33, "error": 91}.get(tone, 39)
        return self._paint(f" {text} ", 1, 7, colour)
