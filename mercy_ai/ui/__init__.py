# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The desktop window, and the startup it shares with the text interface.

The window is driven by the real :class:`~mercy_ai.config.Config` and the real
:class:`~mercy_ai.loop.ControlLoop`; nothing here is a mock.  The modules split
along what needs a window:

* :mod:`mercy_ai.ui.model` is pure.  It converts a ``Config`` to and from the
  plain dictionary the page exchanges over the bridge, checks typed values
  against their ranges, and normalises a captured key.  No threads, no window,
  no I/O -- so it is where the tests live.
* :mod:`mercy_ai.ui.runner` owns the control loop on a background thread and
  publishes a snapshot of it.  Its interesting half, :class:`StatusHub`, is also
  pure and also tested directly.
* :mod:`mercy_ai.ui.bridge` is the object handed to the web view, and the one
  every action goes through.  It is glue, and stays thin on purpose.
* :mod:`mercy_ai.ui.session` is the window-free startup -- reading the config,
  loading templates, building the runner and bridge -- shared with the text
  interface in :mod:`mercy_ai.cli`.
* :mod:`mercy_ai.ui.app` is the only module that imports ``webview``, and it is
  imported lazily, so ``import mercy_ai.ui`` still works with no GUI stack
  installed at all.
"""

from __future__ import annotations

__all__ = ["WEB_DIR"]

from pathlib import Path


def _web_dir() -> Path:
    """Where ``index.html`` lives, both from source and inside a frozen build.

    A frozen build unpacks it under ``sys._MEIPASS`` (see
    :data:`mercy_ai.paths.BUNDLE_DIR`), which for a one-file build is a
    temporary folder rather than the executable's own.
    """
    import sys

    if getattr(sys, "frozen", False):
        from ..paths import BUNDLE_DIR

        return BUNDLE_DIR / "ui" / "web"
    return Path(__file__).resolve().parent / "web"


WEB_DIR: Path = _web_dir()
