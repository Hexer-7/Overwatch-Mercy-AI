# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The image operations Mercy AI needs, over two interchangeable backends.

Mercy AI uses a small part of OpenCV: a colour threshold, a bitwise AND, a
Gaussian blur, a resize that almost never fires, template matching, and PNG
reading and writing.  The normalised cross-correlation in
:mod:`mercy_ai.vision` is pure numpy.  Yet ``cv2.pyd`` is about 71 MB and cannot
be trimmed, because the wheel ships one monolithic binary.

So the eight public functions here wrap those operations, with two
implementations behind them:

``opencv``
    Forwards to ``cv2`` with identical arguments.  Used whenever OpenCV is
    installed, and the reference for behaviour.
``numpy``
    Used when OpenCV is not installed, which is how ``Mercy AI.exe`` is built.
    Roughly 71 MB smaller and, for the blur (the only expensive operation),
    measurably faster.  PNG files are read and written with :mod:`mercy_ai.png`.

The backend is chosen once, at import, by whether ``cv2`` is importable.  There
is no switch and no configuration: a build either carries OpenCV or it does not.

**The two are not bit-identical.**  The blur differs by at most a few 1/255 per
pixel, which moves the final match score by under 2e-3.  It is OpenCV that is
the approximation: raising this implementation's precision moves it *away* from
``cv2``.  ``tests/test_imageops.py`` measures the gap on the real assets.
"""

from __future__ import annotations

import zlib
from functools import lru_cache
from pathlib import Path

import numpy as np

from .png import PngError, decode, encode

__all__ = [
    "BACKEND",
    "HAVE_OPENCV",
    "bitwise_and",
    "gaussian_blur",
    "imread",
    "imwrite",
    "in_range",
    "match_template_max",
    "resize_area",
]

try:  # pragma: no cover - which branch runs depends on the build
    import cv2

    HAVE_OPENCV = True
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment, unused-ignore]
    HAVE_OPENCV = False

#: ``"opencv"`` or ``"numpy"``: which half of this module is in use.
BACKEND = "opencv" if HAVE_OPENCV else "numpy"


# --------------------------------------------------------------------------- #
# Reading and writing
# --------------------------------------------------------------------------- #


def imread(path: Path, *, alpha: bool) -> np.ndarray | None:
    """Read an image as BGR, or BGRA when *alpha*.  ``None`` if unreadable.

    ``None`` rather than an exception, matching ``cv2.imread``;
    :func:`mercy_ai.vision.imread_checked` turns it into a message that names
    the file and says what to do about it.
    """
    if HAVE_OPENCV:
        return cv2.imread(str(path), cv2.IMREAD_UNCHANGED if alpha else cv2.IMREAD_COLOR)
    try:
        image = decode(Path(path).read_bytes())
    except (OSError, PngError, zlib.error):
        return None
    if not alpha and image.shape[2] == 4:
        # IMREAD_COLOR drops the alpha channel rather than compositing it.
        return np.ascontiguousarray(image[:, :, :3])
    return image


def imwrite(path: Path, image: np.ndarray) -> bool:
    """Write a BGR/BGRA image.  Returns whether it was written."""
    if HAVE_OPENCV:
        return bool(cv2.imwrite(str(path), image))
    try:
        Path(path).write_bytes(encode(image))
    except (OSError, PngError):
        return False
    return True


# --------------------------------------------------------------------------- #
# Per-frame arithmetic
# --------------------------------------------------------------------------- #


def in_range(
    image: np.ndarray, lower: np.ndarray, upper: np.ndarray, dst: np.ndarray | None = None
) -> np.ndarray:
    """0/255 mask of pixels whose every channel lies within the bounds.

    Written channel by channel rather than as ``(image >= lower) & (image <=
    upper)``.  The whole-array form is about 18x slower than ``cv2.inRange``: it
    builds three full ``HxWxC`` boolean temporaries and then reduces them, where
    this builds ``HxW`` ones and skips the comparisons that cannot fail.

    Skipping matters more than it sounds.  This program's bounds are
    ``[230, 230, 230, 0]`` to ``[255, 255, 255, 255]`` (alpha is unconstrained
    so a BGRA grab needs no conversion copy), so on ``uint8`` *every* upper
    bound and the alpha lower bound are vacuous: four of the eight comparisons
    do no work at all.
    """
    if HAVE_OPENCV:
        return cv2.inRange(image, lower, upper, dst=dst)
    limits = np.iinfo(image.dtype) if np.issubdtype(image.dtype, np.integer) else None
    inside: np.ndarray | None = None
    for channel in range(image.shape[2]):
        low, high = lower[channel], upper[channel]
        plane = image[:, :, channel]
        needs_low = limits is None or low > limits.min
        needs_high = limits is None or high < limits.max
        if not needs_low and not needs_high:
            continue
        if needs_low and needs_high:
            part = (plane >= low) & (plane <= high)
        elif needs_low:
            part = plane >= low
        else:
            part = plane <= high
        inside = part if inside is None else (inside & part)
    if inside is None:  # every bound was vacuous: the whole image qualifies
        inside = np.ones(image.shape[:2], dtype=bool)
    if dst is None:
        return (inside * np.uint8(255)).astype(np.uint8)
    np.multiply(inside, np.uint8(255), out=dst, casting="unsafe")
    return dst


def bitwise_and(first: np.ndarray, second: np.ndarray, dst: np.ndarray | None = None) -> np.ndarray:
    if HAVE_OPENCV:
        return cv2.bitwise_and(first, second, dst=dst)
    if dst is None:
        return np.bitwise_and(first, second)
    np.bitwise_and(first, second, out=dst)
    return dst


@lru_cache(maxsize=16)
def _blur_matrix(length: int, ksize: int) -> np.ndarray:
    """A separable Gaussian pass expressed as one ``length x length`` matrix.

    A 41-tap blur of a 120x102 region is a small problem with a large constant
    factor, and OpenCV spends most of it dispatching threads.  Folding the kernel and the ``BORDER_REFLECT_101``
    edge handling into a matrix turns the whole pass into a single ``@``, which
    hands it to BLAS -- measured **4x faster than cv2.GaussianBlur** at 120x102
    and 2x at 184x156.

    Built once per image size and cached; there are at most two sizes in a run.
    """
    sigma = 0.3 * ((ksize - 1) * 0.5 - 1) + 0.8  # OpenCV's rule when sigma is 0
    taps = np.exp(-(((np.arange(ksize) - ksize // 2) / sigma) ** 2) / 2)
    taps /= taps.sum()

    matrix = np.zeros((length, length))
    for row in range(length):
        for offset, weight in enumerate(taps):
            source = row + offset - ksize // 2
            # BORDER_REFLECT_101: ... 3 2 1 | 0 1 2 3 | 2 1 0 ...
            if source < 0:
                source = -source
            if source >= length:
                source = 2 * (length - 1) - source
            matrix[row, source] += weight
    return matrix


def gaussian_blur(
    image: np.ndarray, ksize: tuple[int, int], dst: np.ndarray | None = None
) -> np.ndarray:
    """Blur a single-channel or multi-channel uint8 image."""
    if HAVE_OPENCV:
        return cv2.GaussianBlur(image, ksize, 0, dst=dst)
    height, width = image.shape[:2]
    vertical = _blur_matrix(height, ksize[1])
    horizontal = _blur_matrix(width, ksize[0])
    source = image.astype(np.float64)
    if image.ndim == 2:
        blurred = vertical @ source @ horizontal.T
    else:
        blurred = np.stack(
            [vertical @ source[:, :, c] @ horizontal.T for c in range(image.shape[2])], axis=2
        )
    rounded = np.clip(np.floor(blurred + 0.5), 0, 255)
    if dst is None:
        return rounded.astype(np.uint8)
    np.copyto(dst, rounded, casting="unsafe")
    return dst


@lru_cache(maxsize=16)
def _area_matrix(source_length: int, target_length: int) -> np.ndarray:
    """Box-filter weights mapping *source_length* samples onto *target_length*.

    This is what ``INTER_AREA`` means: each output pixel is the average of the
    input interval it covers, with fractional weights at the ends.
    """
    scale = source_length / target_length
    matrix = np.zeros((target_length, source_length))
    for index in range(target_length):
        start, end = index * scale, (index + 1) * scale
        first, last = int(np.floor(start)), min(int(np.ceil(end)), source_length)
        for position in range(first, last):
            overlap = min(end, position + 1) - max(start, position)
            if overlap > 0:
                matrix[index, position] = overlap
        total = matrix[index].sum()
        if total:
            matrix[index] /= total
    return matrix


def resize_area(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Resize to ``(width, height)`` by area averaging."""
    if HAVE_OPENCV:
        return cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    width, height = size
    vertical = _area_matrix(image.shape[0], height)
    horizontal = _area_matrix(image.shape[1], width)
    source = image.astype(np.float64)
    if image.ndim == 2:
        resized = vertical @ source @ horizontal.T
    else:
        resized = np.stack(
            [vertical @ source[:, :, c] @ horizontal.T for c in range(image.shape[2])], axis=2
        )
    if np.issubdtype(image.dtype, np.integer):
        return np.clip(np.floor(resized + 0.5), 0, 255).astype(image.dtype)
    return resized.astype(image.dtype)


def match_template_max(image: np.ndarray, template: np.ndarray) -> float:
    """Best ``TM_CCOEFF_NORMED`` score of *template* within *image*.

    Only the equally sized case is implemented in the numpy backend, because
    that is the only one that occurs: :func:`mercy_ai.vision.fit` has already
    matched the frame to the template, so the correlation surface is 1x1. A
    genuine sliding search would need OpenCV's DFT path to be worth writing, and
    nothing asks for it.
    """
    if HAVE_OPENCV:
        surface = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(surface)[1])
    if image.shape != template.shape:
        raise ValueError(
            f"Mercy AI can only match equally sized images, got {image.shape} and {template.shape}."
        )
    left = image.astype(np.float64).reshape(-1, image.shape[-1] if image.ndim == 3 else 1)
    right = template.astype(np.float64).reshape(left.shape)
    left = left - left.mean(axis=0)
    right = right - right.mean(axis=0)
    numerator = float((left * right).sum())
    denominator = float(np.sqrt((left * left).sum() * (right * right).sum()))
    if denominator == 0.0:
        return 0.0
    if abs(numerator) < denominator:
        return numerator / denominator
    # OpenCV's accuracy guard, reproduced: snap to +/-1 within 12.5 %, else zero.
    return (1.0 if numerator > 0 else -1.0) if abs(numerator) < denominator * 1.125 else 0.0
