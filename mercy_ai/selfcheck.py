# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""``Mercy AI.exe --self-check``: prove a build carries everything it needs.

CI runs this on the exe it has just built. Every module the program imports at
run time must import, the window's page must be there, the templates for every
resolution must load from wherever a one-file build unpacks them, and the text
interface must draw each of its screens. The report goes to ``self-check.txt``
next to the exe, because a windowed build has nowhere to print.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from functools import partial
from pathlib import Path

__all__ = ["BUNDLED_MODULES", "self_check"]

#: What the program imports at run time, some of it lazily, so a module the
#: bundler missed would otherwise only show up on a player's machine.
BUNDLED_MODULES = (
    "numpy",
    "mss",
    "keyboard",
    "pynput.mouse",
    "psutil",
    "win32gui",
    "win32process",
    "webview",
    "webview.platforms.winforms",
    "mercy_ai.cli.app",
    "mercy_ai.cli.console",
    "mercy_ai.cli.capture",
)


def self_check(report: Path | None = None) -> int:
    """Run every check and write the report.  Returns a process exit code."""
    from .paths import APP_DIR, RESOLUTIONS

    lines: list[str] = []
    failed = False

    def record(label: str, action: Callable[[], object]) -> None:
        nonlocal failed
        try:
            action()
            lines.append(f"ok    {label}")
        except BaseException as exc:  # a failed check must still be reported
            failed = True
            lines.append(f"FAIL  {label}: {exc!r}")

    for module in BUNDLED_MODULES:
        record(f"import {module}", partial(importlib.import_module, module))

    def page() -> None:
        from .ui import WEB_DIR

        for name in ("index.html", "app.js", "app.css", "_ds/styles.css"):
            if not (WEB_DIR / name).is_file():
                raise FileNotFoundError(WEB_DIR / name)

    record("ui/web", page)

    def templates(resolution: str) -> None:
        from .assets import load_assets

        load_assets(resolution)

    for resolution in RESOLUTIONS:
        record(f"templates {resolution}", partial(templates, resolution))

    screens: list[str] = []

    def text_interface() -> None:
        from .cli.snapshot import render_all_screens

        screens.append(render_all_screens())

    record("text interface", text_interface)

    lines.append("RESULT " + ("FAIL" if failed else "OK"))
    folder = report.parent if report else APP_DIR
    (report or APP_DIR / "self-check.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if screens:
        (folder / "cli-screens.txt").write_text(screens[0], encoding="utf-8")
    return 1 if failed else 0
