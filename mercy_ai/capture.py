# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""Screen capture: grab the health and Ana-bomb regions with ``mss``.

A single :class:`ScreenGrabber` is created once and reused for every frame.
Constructing an ``mss`` backend on Windows allocates GDI objects (window and
compatible DCs, a bitmap, monitor enumeration), and doing that per frame costs
far more than the pixel copy itself and churns GDI handles.

``mss`` is imported lazily so this module can be imported (and most of it
tested) on a machine without it.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import numpy as np

from .errors import CaptureError
from .geometry import Region, union_region

log = logging.getLogger(__name__)

__all__ = ["CaptureBackend", "CombinedCapture", "ScreenGrabber", "ScreenShotLike"]


# --------------------------------------------------------------------------- #
# The CAPTUREBLT flag dominates capture time
# --------------------------------------------------------------------------- #


def captureblt_enabled() -> bool | None:
    """Whether mss is currently blitting with CAPTUREBLT. ``None`` if unknown."""
    try:
        from mss.windows import gdi
    except ImportError:
        return None
    flag = getattr(gdi, "CAPTUREBLT", None)
    return None if flag is None else bool(flag)


def use_fast_capture(enabled: bool = True) -> bool:
    """Turn mss's CAPTUREBLT flag off (or back on). Returns whether it took effect.

    ``mss`` blits with ``SRCCOPY | CAPTUREBLT``. ``CAPTUREBLT`` asks GDI to
    include layered windows, which forces the desktop compositor to flatten the
    whole desktop before the copy. Its cost barely depends on the region size, so
    on a composited Windows desktop it can dominate everything else: a 120x124
    grab measured at 5227 us/frame -- 86 % of total frame time -- against 807 us
    for the entire vision pipeline.

    Dropping it is not a compromise for this program. Overwatch draws the health
    bar itself, so it is captured either way; what CAPTUREBLT adds is *other*
    applications' translucent overlays, which can only sit on top of the pixels
    we want to read. Excluding them is if anything more correct here.

    ``SRCCOPY`` and ``CAPTUREBLT`` are module globals resolved at call time
    inside ``gdi.grab``, so rebinding the constant is enough -- no reimplemented
    GDI code, and mss's own DIB-section handling is kept intact.
    """
    try:
        from mss.windows import gdi
    except ImportError:
        return False  # not Windows, or an mss layout we do not recognise
    if not hasattr(gdi, "CAPTUREBLT"):
        log.debug("mss has no CAPTUREBLT constant; leaving capture untouched")
        return False
    gdi.CAPTUREBLT = 0 if enabled else 0x40000000
    return True


class ScreenShotLike(Protocol):
    """The part of ``mss``'s ``ScreenShot`` this module uses.

    Declared read-only: ``mss`` exposes these as properties, and a protocol with
    plain mutable attributes would demand settable members it does not have.
    """

    @property
    def raw(self) -> Any: ...

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...


class CaptureBackend(Protocol):
    """The part of ``mss.mss()`` this module uses. Injectable for tests."""

    # Deliberately loose: mss accepts a dict or a 4-tuple, and returns its own
    # ScreenShot. Only ``raw``/``width``/``height`` are ever touched here.
    def grab(self, region: Any, /) -> ScreenShotLike: ...

    def close(self) -> None: ...


def _open_backend() -> CaptureBackend:
    """Construct the real MSS backend. Imported here so the module stays
    importable (and testable) on a machine without mss installed."""
    import mss

    return mss.mss()


class ScreenGrabber:
    """One long-lived capture backend, reused for the life of the process.

    Not thread-safe -- ``mss`` instances never are.  Keep it owned by the capture
    loop, which is already the only thread that captures.
    """

    def __init__(self, backend: CaptureBackend | None = None) -> None:
        self._backend: CaptureBackend = backend if backend is not None else _open_backend()
        self._closed = False

    def grab(self, region: dict[str, int]) -> np.ndarray:
        """Grab *region* as an ``HxWx4`` BGRA array.

        ``np.frombuffer`` gives a view rather than the copy ``np.array`` would
        make (115 KB per grab for a 4K health region, twice per frame).

        The returned view aliases the backend's internal buffer and is only valid
        until the next :meth:`grab`.  Every caller here consumes it immediately;
        anything that needs to keep a frame must copy it.

        A failed grab becomes a :class:`CaptureError` with a message the user can
        act on, rather than a ``ScreenShotError`` traceback from inside the GDI
        wrapper.
        """
        try:
            shot = self._backend.grab(region)
        except Exception as exc:
            raise CaptureError(
                f"Could not capture the screen region "
                f"{region['width']}x{region['height']} at "
                f"({region['left']}, {region['top']}).\n"
                f"Reason: {exc}\n\n"
                f"Common causes:\n"
                f"  - the workstation is locked, or the Remote Desktop session is disconnected\n"
                f"  - Mercy AI is running as a service or scheduled task with no desktop\n"
                f"  - the region falls outside every monitor (check <resolution> in config.xml)"
            ) from exc
        return np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._backend.close()
            except Exception:  # pragma: no cover - backend teardown is best-effort
                log.debug("capture backend close failed", exc_info=True)

    def __enter__(self) -> ScreenGrabber:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class CombinedCapture:
    """One grab, two views.

    The health and Ana-bomb regions are adjacent in every shipped ``info.xml``:
    the bomb box is horizontally inside the health box and starts at its bottom
    edge.  Their union is at most ~19 % more pixels than the health region alone,
    so grabbing it once and slicing costs less than two ``BitBlt`` round trips.

    Containment is *computed*, not assumed: if a future asset set makes the union
    wasteful, this falls back to two separate grabs automatically.
    """

    #: Grab the union while it costs no more than this multiple of the two boxes.
    WASTE_LIMIT = 1.5

    def __init__(self, health: Region, bomb: Region, grabber: ScreenGrabber) -> None:
        self.grabber = grabber
        self.health = health
        self.bomb = bomb
        self.union = union_region(health, bomb)
        separate_area = health.area + bomb.area
        self.combined = self.union.area <= separate_area * self.WASTE_LIMIT
        log.debug(
            "capture mode=%s union=%dx%d (%d px) vs separate %d px",
            "combined" if self.combined else "separate",
            self.union.width,
            self.union.height,
            self.union.area,
            separate_area,
        )

    def frames(self) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(health_frame, bomb_frame)`` as BGRA arrays."""
        if not self.combined:
            # Note the order: the health frame must be copied, because the second
            # grab overwrites the backend buffer the first view points at.
            health = self.grabber.grab(self.health.as_mss()).copy()
            return health, self.grabber.grab(self.bomb.as_mss())

        buffer = self.grabber.grab(self.union.as_mss())
        return self._view(buffer, self.health), self._view(buffer, self.bomb)

    def _view(self, buffer: np.ndarray, region: Region) -> np.ndarray:
        top = region.y - self.union.y
        left = region.x - self.union.x
        return buffer[top : top + region.height, left : left + region.width]
