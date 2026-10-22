# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Project exception types.

``MercyAIError`` and its subclasses are expected, user-fixable failures: the
launcher and the UI show them as a plain message.  Any other exception is a bug
and gets a logged traceback.  Distinct types also keep a configuration problem
from being swallowed by an ``except Exception`` meant for a transient Win32
error.
"""

from __future__ import annotations

__all__ = [
    "AssetError",
    "CaptureError",
    "ConfigError",
    "GeometryError",
    "MercyAIError",
]


class MercyAIError(Exception):
    """Base for expected, user-actionable failures (no traceback shown)."""


class ConfigError(MercyAIError):
    """config.xml is missing, corrupt, or holds an unusable value."""


class AssetError(MercyAIError):
    """A file under data/ is missing, unreadable, or the wrong shape."""


class GeometryError(MercyAIError):
    """info.xml could not be parsed into a pair of capture regions."""


class CaptureError(MercyAIError):
    """The screen could not be captured.

    ``mss`` raises ``ScreenShotError`` for several ordinary Windows conditions:
    no interactive desktop session (a service, or a disconnected RDP session), a
    locked workstation, or a denied ``BitBlt``.  This wraps it in a message that
    tells the user what to do, instead of a traceback from the GDI wrapper.
    """
