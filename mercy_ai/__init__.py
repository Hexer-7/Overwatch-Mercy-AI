# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Mercy AI - Overwatch Mercy pocket-healing assistant.

Importing the package pulls in nothing heavy (no pywin32, no ``keyboard``), so
the pure-logic modules stay importable and testable on any platform.
"""

from __future__ import annotations

#: The one place the version is written. pyproject.toml and the exe's version
#: resource (mercy_ai.spec) both read it from here.
__version__ = "1.5.3"

__author__ = "Hexer-7"
__copyright__ = "Copyright (c) 2024-2026 Hexer-7"

__all__ = ["__author__", "__copyright__", "__version__"]
