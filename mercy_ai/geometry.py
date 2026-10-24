# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Capture-region geometry loaded from ``data/<res>/info.xml``.

:func:`load_geometry` returns the health and Ana-bomb regions, and the weapon
icon region when the file has one, as one immutable :class:`Geometry`, which
callers pass explicitly to whatever needs it.

The XML tag is ``<high>``; the Python attribute is ``height``.  Existing
``info.xml`` files keep working, and every reader of the code sees the word it
expects.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .errors import GeometryError

__all__ = ["Geometry", "Region", "load_geometry", "union_region"]


@dataclass(frozen=True)
class Region:
    """A rectangle in absolute virtual-desktop pixels."""

    x: int
    y: int
    width: int
    height: int

    def as_mss(self) -> dict[str, int]:
        """Convert to the dict shape ``mss.grab`` expects."""
        return {"left": self.x, "top": self.y, "width": self.width, "height": self.height}

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass(frozen=True)
class Geometry:
    """The regions Mercy AI watches."""

    health: Region
    bomb: Region
    #: The weapon icon beside the ammo counter, for the staff check.  Optional:
    #: an ``info.xml`` without ``<weapon_res>`` still loads, without the check.
    weapon: Region | None = None


def union_region(a: Region, b: Region) -> Region:
    """Smallest rectangle covering both.

    The two regions are adjacent in every shipped ``info.xml``, so their union
    can be grabbed once instead of twice.
    """
    left = min(a.x, b.x)
    top = min(a.y, b.y)
    right = max(a.right, b.right)
    bottom = max(a.bottom, b.bottom)
    return Region(x=left, y=top, width=right - left, height=bottom - top)


def _region_from(node: ET.Element, tag: str, source: Path) -> Region:
    def field(name: str) -> int:
        text = node.findtext(name)
        if text is None:
            raise GeometryError(f"<{tag}> in {source} is missing <{name}>.")
        try:
            return int(text.strip())
        except ValueError as exc:
            raise GeometryError(f"<{tag}><{name}> in {source} is not an integer: {text!r}") from exc

    region = Region(x=field("x"), y=field("y"), width=field("width"), height=field("high"))
    if region.width <= 0 or region.height <= 0:
        raise GeometryError(f"<{tag}> in {source} has a non-positive size: {region}")
    return region


def load_geometry(xml_path: Path) -> Geometry:
    """Parse ``info.xml`` into its regions.

    Elements are tested with ``is None`` rather than truthiness:
    ``Element.__bool__`` means "has children", not "exists", and Python 3.12+
    deprecates it.
    """
    if not xml_path.is_file():
        raise GeometryError(f"Missing info.xml file: {xml_path}")
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        raise GeometryError(f"{xml_path} is not valid XML: {exc}") from exc

    res_element = root.find("res")
    bomb_res_element = root.find("bomb_res")
    if res_element is None or bomb_res_element is None:
        raise GeometryError(f"<res> and/or <bomb_res> missing in {xml_path}.")

    weapon_element = root.find("weapon_res")
    return Geometry(
        health=_region_from(res_element, "res", xml_path),
        bomb=_region_from(bomb_res_element, "bomb_res", xml_path),
        weapon=(
            None if weapon_element is None else _region_from(weapon_element, "weapon_res", xml_path)
        ),
    )
