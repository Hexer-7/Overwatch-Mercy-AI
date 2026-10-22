# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""``python -m mercy_ai``, and the script the built exe runs.

Imports are absolute on purpose: PyInstaller runs this file as a top-level
script, where there is no parent package for a relative import to use.
"""

from __future__ import annotations

from mercy_ai.launch import main

if __name__ == "__main__":
    raise SystemExit(main())
