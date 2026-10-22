# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Draw every screen of the text interface without a console.

Used by ``--self-check`` (so CI proves the built exe can draw the interface)
and by the tests. Nothing here opens a console, takes the one-copy lock,
installs a hook or reads ``config.xml``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .. import __version__
from ..config import Config
from ..ui.model import config_to_ui
from .state import Data, Edit, Message, Modal, Setup, UIState
from .style import ASCII, UNICODE, Style
from .text import cell_width, strip_ansi

__all__ = ["check_frame", "render_all_screens", "sample_data", "sample_screens"]

#: Keys used in samples. Never a suggestion: the program itself suggests none.
SAMPLE_KEYS: dict[str, Any] = {"heal_btn": "p", "damage_boost_btn": "o"}


#: Every optional feature on, so Advanced draws all of its rows.
ALL_ON: dict[str, Any] = {
    "dynamic_heal": True,
    "flash_heal_trigger": "expiry",
    "flash_heal_on_health_drop": True,
    "flash_heal_health_mode": "low",
}


def sample_data(*, setup: bool = False, running: bool = True, all_on: bool = False) -> Data:
    if setup:
        config = Config(resolution="1440", heal_btn="", damage_boost_btn="")
    else:
        config = Config(resolution="1440", **SAMPLE_KEYS, **(ALL_ON if all_on else {}))
    status = {
        "running": running,
        "live": False,
        "paused": False,
        "state_word": "RUNNING" if running else "STOPPED",
        "mode_word": "HEAL",
        "health": 0.734 if running else 0.0,
        "bomb": 0.0,
        "fps": 141.0 if running else 0.0,
        "fps_avg": 143.2 if running else 0.0,
        "flash": "ready" if running else None,
        "uptime": "12:04",
        "error": None,
    }
    env = {
        "needs_setup": setup,
        "elevated": True,
        "detected": "1440",
        "primary": "2560x1440",
        "resolutions": ["2160", "1440", "1080", "768"],
        "config_error": None,
    }
    return Data(config=config_to_ui(config), status=status, env=env, version=__version__)


def sample_screens() -> list[tuple[str, UIState, Data]]:
    """(name, state, data) for every screen worth drawing."""
    main = sample_data()
    stopped = sample_data(running=False)
    stopped.status["error"] = (
        "The templates for 1440p could not be loaded, so there is nothing to match against. "
        "Pick another resolution."
    )
    mismatch = sample_data(running=False)
    mismatch.env.update(detected="1080", primary="1920x1080")
    first = sample_data(setup=True)
    everything = sample_data(all_on=True)
    screens = [
        ("status", UIState(), main),
        ("status, stopped with a problem", UIState(), stopped),
        ("status, resolution mismatch", UIState(), mismatch),
        ("settings", UIState(tab=1), main),
        ("settings, mismatch", UIState(tab=1), mismatch),
        ("advanced", UIState(tab=1, advanced=True), main),
        (
            "advanced, typing",
            UIState(
                tab=1,
                advanced=True,
                focus={"advanced": "dynamic_heal_drop"},
                edit=Edit("dynamic_heal_drop", "0.2"),
            ),
            everything,
        ),
        (
            "advanced, Flash Heal",
            UIState(tab=1, advanced=True, focus={"advanced": "flash_heal_low_health"}),
            everything,
        ),
        ("help", UIState(tab=2), main),
        ("about", UIState(tab=3), main),
        ("setup 1", UIState(screen="setup", setup=Setup(step=1, resolution="1440")), first),
        (
            "setup 2",
            UIState(screen="setup", setup=Setup(step=2, ack=True, resolution="1440")),
            first,
        ),
        (
            "setup 3",
            UIState(screen="setup", setup=Setup(step=3, ack=True, resolution="1440")),
            first,
        ),
        (
            "key capture",
            UIState(tab=1, modal=Modal("capture", field="pause_toggle_button")),
            main,
        ),
        (
            "trigger",
            UIState(tab=1, advanced=True, modal=Modal("trigger", field="keybind_btn")),
            main,
        ),
        ("quit", UIState(modal=Modal("quit")), main),
        (
            "message",
            UIState(
                tab=1,
                message=Message(
                    "F2 is already your pause key. Pick a different heal key.", "error"
                ),
            ),
            main,
        ),
    ]
    return screens


_EDITION = re.compile(r"Mercy AI [A-Z]\w*")


def check_frame(lines: list[str], cols: int, rows: int) -> list[str]:
    """What is wrong with a drawn frame, if anything."""
    problems = []
    if len(lines) != rows:
        problems.append(f"{len(lines)} lines instead of {rows}")
    for number, line in enumerate(lines, start=1):
        limit = cols - 1 if number == rows else cols
        if cell_width(line) > limit:
            problems.append(f"line {number} is {cell_width(line)} cells wide (limit {limit})")
    text = "\n".join(strip_ansi(line) for line in lines)
    if "Lite" in text:
        problems.append("says 'Lite'")
    if edition := _EDITION.search(text):
        problems.append(f"capitalised word after the name: {edition.group(0)!r}")
    return problems


def render_all_screens(sizes: tuple[tuple[int, int], ...] = ((80, 25), (120, 30))) -> str:
    """Every screen at every size, as plain text. Raises if any frame is broken."""
    from .screens import render

    out: list[str] = []
    failures: list[str] = []
    for glyphs in (UNICODE, ASCII):
        style = Style(color=True, glyphs=glyphs)
        for cols, rows in sizes:
            for name, state, data in sample_screens():
                state = replace(state, cols=cols, rows=rows)
                lines = render(state, data, style).lines
                for problem in check_frame(lines, cols, rows):
                    failures.append(f"{name} at {cols}x{rows}: {problem}")
                if glyphs is ASCII and any(
                    ord(c) > 127 for line in lines for c in strip_ansi(line)
                ):
                    failures.append(f"{name} at {cols}x{rows}: non-ASCII in ASCII mode")
                out.append(
                    f"=== {name} ({cols}x{rows}, {'unicode' if glyphs is UNICODE else 'ascii'}) ==="
                )
                out += [strip_ansi(line).rstrip() for line in lines]
    if failures:
        raise AssertionError("\n".join(failures))
    return "\n".join(out) + "\n"
