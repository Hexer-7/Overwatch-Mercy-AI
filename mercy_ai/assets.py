# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Loading and validating everything under ``data/<resolution>/``.

Every file is checked up front, so a missing asset produces one actionable
message instead of a cryptic decoder error deep in the pipeline.  On a
case-sensitive filesystem this also catches a wrongly cased filename such as
``WHITE.png``; on Windows ``Path.is_file()`` ignores case, so it does not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .errors import AssetError
from .geometry import Geometry, load_geometry
from .paths import DATA_DIR, asset_paths
from .vision import MatchBuffers, Template, build_template, load_filter

log = logging.getLogger(__name__)

__all__ = ["Assets", "load_assets", "validate_assets"]


@dataclass(frozen=True)
class Assets:
    """Everything the capture loop needs, precomputed once at startup."""

    resolution: str
    geometry: Geometry
    health: Template
    bomb: Template
    health_buffers: MatchBuffers
    bomb_buffers: MatchBuffers


def validate_assets(resolution: str, data_dir: Path | None = None) -> dict[str, Path]:
    """Check all five files exist before anything tries to decode them."""
    paths = asset_paths(resolution, data_dir or DATA_DIR)
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise AssetError(
            f"Missing data files for resolution {resolution}:\n  "
            + "\n  ".join(missing)
            + "\n\nRe-download the 'data' folder from the GitHub release."
        )
    return paths


def load_assets(resolution: str, data_dir: Path | None = None) -> Assets:
    """Load geometry, filters and templates for one resolution.

    Images are read through :mod:`mercy_ai.imageops`: with OpenCV when it is
    installed, otherwise with :mod:`mercy_ai.png` (as in the shipped exe).
    Either way they arrive in BGR/BGRA order with alpha at index 3.
    """
    paths = validate_assets(resolution, data_dir)
    geometry = load_geometry(paths["info"])

    templates: dict[str, Template] = {}
    for name, region, image_key, filter_key, blur in (
        ("health", geometry.health, "health", "health_filter", True),
        ("bomb", geometry.bomb, "bomb", "bomb_filter", False),
    ):
        filter_spec = load_filter(paths[filter_key])
        if not filter_spec.is_fast_path:
            # Never silently change matching behaviour because an asset no longer
            # meets the assumption the fast path relies on.
            log.warning(
                "%s is not a black/binary-alpha filter; falling back to the original "
                "four-channel matchTemplate path for the %s region.",
                filter_spec.path,
                name,
            )
        templates[name] = build_template(
            paths[image_key],
            filter_spec,
            width=region.width,
            height=region.height,
            blur=blur,
            name=name,
        )

    return Assets(
        resolution=resolution,
        geometry=geometry,
        health=templates["health"],
        bomb=templates["bomb"],
        health_buffers=MatchBuffers.for_template(templates["health"]),
        bomb_buffers=MatchBuffers.for_template(templates["bomb"]),
    )
