# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Filesystem locations, anchored to the program rather than to the CWD.

A shortcut or "Run as administrator" (which sets the CWD to
``C:\\Windows\\System32``) can launch the program from any working directory,
and the ``keyboard`` library needs elevation to reach Overwatch, so relative
paths would routinely resolve to the wrong place.  Everything is resolved once
here instead: relative to the executable when frozen, and to the source tree
otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = [
    "APP_DIR",
    "ASSET_KEYS",
    "BUNDLE_DIR",
    "CONFIG_PATH",
    "DATA_DIR",
    "RESOLUTIONS",
    "RESOLUTION_SIZES",
    "WEAPON_ASSET_KEYS",
    "WEBVIEW_DATA_DIR",
    "asset_paths",
]

#: Supported game resolutions, identified by their vertical pixel count.
RESOLUTIONS: tuple[str, ...] = ("2160", "1440", "1080", "768")

#: What each of those numbers *means*, for anywhere a human reads it.
#:
#: The setting is a screen **height**. A user on a 1440x900 monitor might pick
#: '1440' because their screen is 1440 pixels wide; the capture region for a
#: 2560x1440 screen then lands below the bottom of it and the grab fails with
#: an unhelpful BitBlt error. Showing the full size avoids that mistake.
RESOLUTION_SIZES: dict[str, str] = {
    "2160": "3840 x 2160",
    "1440": "2560 x 1440",
    "1080": "1920 x 1080",
    "768": "1366 x 768",
}

#: Logical name -> filename for every per-resolution asset.
ASSET_KEYS: dict[str, str] = {
    "health": "white.png",
    "health_filter": "white_filter.png",
    "bomb": "ana_bomb.png",
    "bomb_filter": "ana_bomb_filter.png",
    "info": "info.xml",
}

#: The weapon icons for the staff check. Kept apart from :data:`ASSET_KEYS`
#: because they are optional: without them only that check is lost.
WEAPON_ASSET_KEYS: dict[str, str] = {
    "blaster": "blaster.png",
    "staff": "staff.png",
}


def _app_dir() -> Path:
    """Directory that owns ``config.xml`` and everything else Mercy AI writes.

    The folder the executable sits in when frozen, the source tree otherwise.
    Nothing is written anywhere else -- not to AppData, not to the user profile.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_dir() -> Path:
    """Directory holding the read-only files shipped with the program.

    A one-file build carries ``data/`` and ``ui/web/`` inside the executable and
    unpacks them to ``sys._MEIPASS`` at launch, so they are no longer next to
    the ``.exe``. A one-folder build sets ``_MEIPASS`` to its own folder, and a
    source checkout has no ``_MEIPASS`` at all, so this covers all three.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", _app_dir())).resolve()
    return Path(__file__).resolve().parent.parent


APP_DIR: Path = _app_dir()
BUNDLE_DIR: Path = _bundle_dir()
DATA_DIR: Path = BUNDLE_DIR / "data"
CONFIG_PATH: Path = APP_DIR / "config.xml"
#: The window's WebView2 profile. pywebview would otherwise put it in AppData
#: (or in %TEMP%); it is created on launch and deleted again on close.
WEBVIEW_DATA_DIR: Path = APP_DIR / "webview_data"


def asset_paths(resolution: str, data_dir: Path | None = None) -> dict[str, Path]:
    """Return the five asset paths for *resolution*.

    ``data_dir`` is injectable so tests can point at a fixture tree without
    touching the real ``data/`` folder.
    """
    base = (data_dir or DATA_DIR) / resolution
    return {key: base / filename for key, filename in ASSET_KEYS.items()}
