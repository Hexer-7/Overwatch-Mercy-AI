# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Which weapon Mercy is holding, read off the icon beside the ammo counter.

Flash Heal is cast from the staff, and its key does something else the rest
of the time: with the Caduceus Blaster out it reloads the gun, and on another
hero it is that hero's own key.  So Flash Heal is pressed **only while the staff
is seen**.  The HUD says which weapon is out: a long thin staff icon (followed
by the two beam icons) for the staff, a compact pistol icon (followed by the
ammo count) for the blaster.

The icon is scored against both icons with one normalised cross-correlation,
like the health ring, but on its **outline** rather than on its white pixels.

Why edges and not white
-----------------------
The HUD is half see-through.  Over a dark background the icon is white on
grey and a white mask finds it at once, but over sun glare, snow or a white
floor the whole box turns white and the mask has nothing to separate: real
frames like that scored *below zero* against the blaster.  What stays visible
on every background is the thin grey outline the game draws around the icon.
So the frame is turned into a map of how sharply brightness changes
(:func:`edge_map`), lightly blurred, and that map is compared.

Measured on real frames at all four resolutions, over dark and white
backgrounds, sun glare, an enemy's outline and the red CRITICAL label: the
staff scored 0.84 to 1.0 against its own icon and at most 0.54 against the
blaster's, and the blaster 0.81 to 1.0 against its own and at most 0.52
against the staff's.  No other part of the screen scored above 0.44.

**A light blur.**  :data:`EDGE_BLUR` is a few pixels wide rather than the
health ring's 41, enough to absorb the soft, half-transparent edges and a pixel
of misalignment without blurring the pistol into the staff.

Only a confident staff reading lets Flash Heal through.  Anything else -- the
blaster, another hero, a menu or the scoreboard over the screen -- holds it
back, so a reading that fails costs a Flash Heal, never a reload.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import imageops
from .errors import AssetError
from .geometry import Region
from .vision import Template, fit, imread_checked, ncc_same_size

log = logging.getLogger(__name__)

__all__ = [
    "EDGE_BLUR",
    "FRESH_READ",
    "READ_INTERVAL",
    "STAFF_HOLD",
    "StaffWatch",
    "WeaponIcons",
    "WeaponReading",
    "build_icon_template",
    "edge_map",
    "read_weapon",
]

#: Small on purpose; see the module docstring.
EDGE_BLUR = (5, 5)

#: A staff sighting still counts this long through readings that show neither
#: weapon, so one unreadable frame mid-fight does not throw away Flash Heal.
#: A reading that shows the blaster ends it at once.
STAFF_HOLD = 0.1
#: How often the icon is read while Flash Heal is on.  A second grab every frame
#: would cost a few hundred frames a second something real; 50 reads a second
#: are plenty for a weapon swap, and a press always reads it fresh anyway.
READ_INTERVAL = 0.02
#: A reading younger than this counts as fresh at the moment of a press.
FRESH_READ = 0.005


def edge_map(frame: np.ndarray) -> np.ndarray:
    """How sharply brightness changes at each pixel of a BGR or BGRA frame, 0-255.

    The brightness is the plain mean of the three colour channels; the change
    is the central difference across and down, combined.  Pixels on the border
    have no neighbour on one side and read as no change.
    """
    gray = frame[:, :, :3].mean(axis=2)
    across = np.zeros_like(gray)
    down = np.zeros_like(gray)
    across[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    down[1:-1, :] = gray[2:, :] - gray[:-2, :]
    return np.clip(np.hypot(across, down), 0, 255).astype(np.uint8)


def _prepare(frame: np.ndarray) -> np.ndarray:
    return imageops.gaussian_blur(edge_map(frame), EDGE_BLUR).astype(np.float64)


def build_icon_template(path: Path, *, width: int, height: int, name: str) -> Template:
    """Load one icon and precompute what :func:`ncc_same_size` needs."""
    prepared = _prepare(fit(imread_checked(path), (width, height)))
    flat = prepared.ravel()
    centered = flat - flat.mean()
    norm = math.sqrt(float(np.dot(centered, centered)))
    if norm < 1e-12:
        raise AssetError(
            f"The {name} icon ({path}) has no outline to match. Re-download the 'data' folder."
        )
    return Template(
        name=name,
        width=width,
        height=height,
        blur=True,
        fast_path=True,
        keep_mask=None,
        centered=centered,
        norm=norm,
        rgba_template=None,
        filter_rgba=None,
    )


@dataclass(frozen=True)
class WeaponIcons:
    """Where the weapon icon is, and what each weapon's icon looks like."""

    region: Region
    blaster: Template
    staff: Template


@dataclass(frozen=True)
class WeaponReading:
    """How closely the icon matches each weapon, from -1 to 1."""

    blaster: float
    staff: float

    def is_blaster(self, threshold: float) -> bool:
        """A clear blaster: past the threshold *and* closer to it than to the staff."""
        return self.blaster >= threshold and self.blaster > self.staff

    def is_staff(self, threshold: float) -> bool:
        """A clear staff: past the threshold *and* closer to it than to the blaster."""
        return self.staff >= threshold and self.staff > self.blaster

    def describe(self, threshold: float) -> str:
        """``staff``, ``blaster`` or ``none``, for status lines."""
        if self.is_staff(threshold):
            return "staff"
        return "blaster" if self.is_blaster(threshold) else "none"


def read_weapon(frame: np.ndarray, icons: WeaponIcons) -> WeaponReading:
    """Score one captured weapon region against both icons."""
    prepared = _prepare(fit(frame, (icons.region.width, icons.region.height)))
    return WeaponReading(
        blaster=ncc_same_size(prepared, icons.blaster),
        staff=ncc_same_size(prepared, icons.staff),
    )


class StaffWatch:
    """Answers "is the staff out?" for the loop, cheaply and conservatively.

    The icon is read at most every :data:`READ_INTERVAL` seconds, and again on
    demand right before a Flash Heal press.  The answer is "yes" while the
    latest weapon seen was the staff and that was under :data:`STAFF_HOLD`
    seconds ago; a blaster reading turns it to "no" at once.

    ``grab`` returns the weapon region as a BGR/BGRA array; ``clock`` and
    ``read`` are injectable so the timing can be tested without a screen.
    """

    def __init__(
        self,
        icons: WeaponIcons,
        grab: Callable[[], np.ndarray],
        *,
        threshold: float,
        clock: Callable[[], float] | None = None,
        read: Callable[[np.ndarray, WeaponIcons], WeaponReading] = read_weapon,
    ) -> None:
        self.icons = icons
        self.threshold = threshold
        self._grab = grab
        self._read = read
        self._clock = clock or time.perf_counter
        self.reading: WeaponReading | None = None
        self._read_at: float | None = None
        self._staff_at: float | None = None

    def _refresh(self, now: float) -> None:
        self.reading = self._read(self._grab(), self.icons)
        self._read_at = now
        if self.reading.is_staff(self.threshold):
            self._staff_at = now
        elif self.reading.is_blaster(self.threshold):
            self._staff_at = None

    def _answer(self, now: float) -> bool:
        return self._staff_at is not None and now - self._staff_at < STAFF_HOLD

    def staff_out(self, now: float | None = None) -> bool:
        """The routine check, once per loop frame."""
        now = self._clock() if now is None else now
        if self._read_at is None or now - self._read_at >= READ_INTERVAL:
            self._refresh(now)
        return self._answer(now)

    def confirm(self, now: float | None = None) -> bool:
        """The check right before a press: reads the icon again unless it just did."""
        now = self._clock() if now is None else now
        if self._read_at is None or now - self._read_at >= FRESH_READ:
            self._refresh(now)
        return self._answer(now)

    def reset(self) -> None:
        """Forget everything; the next check reads the icon again."""
        self.reading = None
        self._read_at = None
        self._staff_at = None
