# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Creating the window.

The only module that imports ``webview``, and it is imported inside the function
so the rest of the package -- and the tests -- never need a GUI stack present.

Why a web view rather than Tkinter or Qt: the window is rendered by the WebView2
runtime, which is a *Windows component* that ships with the OS and with Edge.
Nothing browser-shaped is bundled, so the frozen build grows by the size of the
``pywebview`` package and no more.  Qt would add well over a hundred megabytes,
and ``mercy_ai.spec`` excludes Tkinter to keep the build small.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..errors import MercyAIError
from ..paths import WEBVIEW_DATA_DIR
from . import WEB_DIR
from .session import open_session

log = logging.getLogger(__name__)

__all__ = ["run_gui"]

WINDOW_WIDTH = 520
WINDOW_HEIGHT = 700

WEBVIEW2_HELP = (
    "Mercy AI's window needs Microsoft's WebView2 runtime, which is part of "
    "Windows and of Edge but can be missing on a stripped-down install.\n"
    "Install the free 'Evergreen Bootstrapper' from\n"
    "  https://developer.microsoft.com/microsoft-edge/webview2/\n"
    "and start Mercy AI again. Until then the text version works without it: open a\n"
    'command prompt in Mercy AI\'s folder and run:  "Mercy AI.exe" -cli'
)


def _webview_storage() -> str | None:
    """Keep the WebView2 profile in the program's own folder.

    Left to itself pywebview puts it in a fresh ``%TEMP%`` folder (AppData)
    every launch. Given a path in private mode it uses that instead and deletes
    it again when the window closes. ``None`` -- pywebview's default -- only if
    the program folder cannot be written to, because a window that will not
    open is worse than a temporary folder.
    """
    try:
        WEBVIEW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = WEBVIEW_DATA_DIR / ".write_test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        log.warning("%s is not writable; the window will use a temporary folder", WEBVIEW_DATA_DIR)
        return None
    return str(WEBVIEW_DATA_DIR)


def run_gui(config_path: Path | None = None) -> int:
    """Open the window and block until it closes.  Returns a process exit code."""
    import webview

    session = open_session(config_path)
    bridge, runner = session.bridge, session.runner

    from ..winutil import WINDOW_TITLE

    window = webview.create_window(
        WINDOW_TITLE,
        str(WEB_DIR / "index.html"),
        js_api=bridge,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        resizable=False,
        frameless=True,
        # Shown by Bridge.ready() once the page has painted, so the user
        # never sees the web view's blank first frame.
        hidden=True,
        # The title bar is drawn by the page, so easy_drag would fight it: it
        # makes the whole window a drag handle, including the sliders.
        easy_drag=False,
        background_color="#f2f2f3",
    )

    if window is None:  # pragma: no cover - only if webview itself refuses
        raise MercyAIError(WEBVIEW2_HELP)
    bridge._attach(window)

    def teardown() -> None:
        # Closing the window must never leave a key held down in the game.
        runner.stop()

    window.events.closed += teardown

    try:
        webview.start(
            bridge._watchdog, (window,), private_mode=True, storage_path=_webview_storage()
        )
    except Exception as exc:
        runner.stop()
        text = str(exc)
        if "WebView2" in text or "Edge" in text or "clr" in text.lower():
            raise MercyAIError(WEBVIEW2_HELP) from exc
        raise
    return 0
