# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The vision pipeline: score a captured screen region against a template.

Each region is scored as ``inRange`` (white pixels) -> ``bitwise_and`` with the
region's filter mask -> optional Gaussian blur -> one normalised
cross-correlation (``TM_CCOEFF_NORMED``) on a single channel.  The template side
is precomputed once at startup.

Why one channel and one ``bitwise_and`` are enough
--------------------------------------------------
The straightforward pipeline would convert colour spaces, alpha-composite each
``*_filter.png`` over the mask, and run a four-channel ``matchTemplate``.  Three
facts about the shipped assets make that collapse to the version above:

1. **Every ``*_filter.png`` is pure black with alpha strictly in {0, 255}.**
   Compositing it over a grey-to-RGBA mask therefore reduces to "keep the pixel
   where filter alpha is 0, force it black where alpha is 255", i.e. a single
   ``bitwise_and``.  With no intermediate alpha the result is *bit-exact*.

2. **``bitwise_and`` -> grey -> ``threshold(0)`` is the identity on the mask.**
   ``inRange`` already emits 0/255; a surviving pixel has every channel in
   [230, 255], so its luma is >= 230 > 0, and a rejected pixel is all zeros.
   The bounds are channel-symmetric, so channel order does not matter, and a
   four-element bound ignores the alpha of a BGRA grab without a copy.

3. **Only one channel carries information.**  After (1) the composite has
   R = G = B and a constant alpha.  For ``TM_CCOEFF_NORMED`` OpenCV subtracts a
   per-channel mean and sums across channels, so the constant alpha contributes
   zero and the three identical colour channels contribute a factor of 3 that
   cancels.  Blurring three identical channels is likewise redundant.

Fidelity
--------
The direct NCC agrees with ``cv2.matchTemplate`` to about 2e-4 (worst case over
randomised inputs).  The gap is OpenCV's float32 accumulation; this code
accumulates in float64 and is the more accurate of the two.  Scores are compared
against thresholds in the 0.2-0.99 range, so the difference can only matter for
a score sitting exactly on a threshold, where the decision is arbitrary anyway.

A filter that is *not* black-with-binary-alpha is detected at load time and uses
a general four-channel ``matchTemplate`` path instead, so an asset change can
never silently alter matching behaviour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import imageops
from .errors import AssetError

__all__ = [
    "GAUSS_KSIZE",
    "LOWER_BGR",
    "LOWER_BGRA",
    "UPPER_BGR",
    "UPPER_BGRA",
    "WHITE_LOWER",
    "WHITE_UPPER",
    "FilterSpec",
    "MatchBuffers",
    "Template",
    "build_template",
    "imread_checked",
    "load_filter",
    "ncc_same_size",
    "prepare_binary",
    "score_region",
    "white_mask",
]

#: A health-bar pixel counts as "white" from this value up, on every channel.
WHITE_LOWER = 230
WHITE_UPPER = 255

#: sigma is derived by OpenCV: 0.3 * ((41 - 1) * 0.5 - 1) + 0.8 = 6.5.
#: The blur turns the crisp bar into a smooth intensity profile so that a bar
#: which is *nearly* full still correlates highly with a full one.
GAUSS_KSIZE = (41, 41)

LOWER_BGR = np.array([WHITE_LOWER] * 3, dtype=np.uint8)
UPPER_BGR = np.array([WHITE_UPPER] * 3, dtype=np.uint8)
#: Alpha is left unconstrained so a BGRA grab needs no conversion copy.
LOWER_BGRA = np.array([WHITE_LOWER, WHITE_LOWER, WHITE_LOWER, 0], dtype=np.uint8)
UPPER_BGRA = np.array([WHITE_UPPER, WHITE_UPPER, WHITE_UPPER, 255], dtype=np.uint8)

_MIN_TEMPLATE_NORM = 1e-12


def imread_checked(path: Path, *, alpha: bool = False) -> np.ndarray:
    """Read an image, raising a readable :class:`AssetError` if it fails.

    The underlying reader returns ``None`` for a missing *or corrupt* file;
    passing that on would fail later with an error that says nothing about the
    actual problem.
    """
    image = imageops.imread(path, alpha=alpha)
    if image is None:
        raise AssetError(
            f"Could not read '{path}'. The file is missing or is not a valid image.\n"
            f"Re-download the 'data' folder from the GitHub release."
        )
    return image


def white_mask(image: np.ndarray, dst: np.ndarray | None = None) -> np.ndarray:
    """Binary 0/255 mask of white-ish pixels, for BGR or BGRA input .

    Never copies the input.  Do not be tempted to write ``image[:, :, :3]``
    instead: that slice cannot be expressed as a 3-channel ``cv::Mat`` over the
    same memory, so OpenCV would have to copy it back.
    """
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError(f"Expected an HxWx3 or HxWx4 image, got shape {image.shape}.")
    if image.shape[2] == 4:
        return imageops.in_range(image, LOWER_BGRA, UPPER_BGRA, dst=dst)
    return imageops.in_range(image, LOWER_BGR, UPPER_BGR, dst=dst)


@dataclass(frozen=True)
class FilterSpec:
    """A loaded ``*_filter.png``, in whichever form it qualifies for.

    Exactly one of ``keep_mask`` (fast path) and ``rgba`` (general fallback) is
    not ``None``.
    """

    path: Path
    width: int
    height: int
    keep_mask: np.ndarray | None
    rgba: np.ndarray | None

    @property
    def is_fast_path(self) -> bool:
        return self.keep_mask is not None


def load_filter(path: Path) -> FilterSpec:
    """Load a filter PNG once, at startup, so no file I/O happens per frame."""
    rgba = imread_checked(path, alpha=True)
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise AssetError(
            f"'{path}' has no alpha channel, so it cannot be used as a filter. "
            f"Re-download the 'data' folder."
        )
    height, width = rgba.shape[:2]
    alpha = rgba[:, :, 3]
    rgb_is_black = not rgba[:, :, :3].any()
    alpha_is_binary = bool(np.isin(alpha, (0, 255)).all())
    if rgb_is_black and alpha_is_binary:
        keep = np.where(alpha == 0, np.uint8(255), np.uint8(0)).astype(np.uint8)
        return FilterSpec(path=path, width=width, height=height, keep_mask=keep, rgba=None)
    # An asset that breaks the assumption must not silently take the fast path;
    # use the general four-channel path instead.
    return FilterSpec(
        path=path,
        width=width,
        height=height,
        keep_mask=None,
        rgba=np.ascontiguousarray(rgba),
    )


@dataclass(frozen=True)
class Template:
    """A match target with everything constant precomputed.

    ``cv2.matchTemplate`` would recompute the template's mean and norm on every
    call, hundreds of times a second.
    """

    name: str
    width: int
    height: int
    blur: bool
    fast_path: bool
    keep_mask: np.ndarray | None
    #: float64, flattened, template minus its own mean.  Fast path only.
    centered: np.ndarray | None
    #: sqrt(sum(centered**2)).  Fast path only.
    norm: float
    #: Fallback path only: the RGBA composite to match against.
    rgba_template: np.ndarray | None
    #: Fallback path only: the raw filter, re-composited onto each frame.
    filter_rgba: np.ndarray | None


class MatchBuffers:
    """Reusable scratch so the steady-state loop allocates nothing.

    Without these, every ``inRange``/``bitwise_and``/``GaussianBlur``/``astype``
    would allocate a fresh array: thousands per second at a few hundred frames a
    second, which shows up as latency jitter.
    """

    __slots__ = ("blurred", "f64", "height", "mask", "masked", "width")

    def __init__(self, height: int, width: int) -> None:
        self.height = height
        self.width = width
        self.mask = np.empty((height, width), np.uint8)
        self.masked = np.empty((height, width), np.uint8)
        self.blurred = np.empty((height, width), np.uint8)
        self.f64 = np.empty((height, width), np.float64)

    @classmethod
    def for_template(cls, template: Template) -> MatchBuffers:
        return cls(template.height, template.width)


def _alpha_over(background_gray: np.ndarray, filter_rgba: np.ndarray) -> np.ndarray:
    """General 'source over' composite for the non-conforming-filter fallback.

    Only reachable when a filter is not black-with-binary-alpha, which no shipped
    asset is.  Uses the standard float formula; may differ from PIL's integer
    implementation by a least-significant bit.  The caller warns when this path
    is taken.
    """
    src_a = filter_rgba[:, :, 3].astype(np.float32) / 255.0
    out = np.empty((*background_gray.shape, 4), np.uint8)
    inv = 1.0 - src_a
    bg = background_gray.astype(np.float32)
    for channel in range(3):
        blended = filter_rgba[:, :, channel].astype(np.float32) * src_a + bg * inv
        out[:, :, channel] = np.clip(np.rint(blended), 0, 255).astype(np.uint8)
    out[:, :, 3] = 255  # dst alpha is 255, so the composite is always opaque
    return out


def build_template(
    image_path: Path,
    filter_spec: FilterSpec,
    *,
    width: int,
    height: int,
    blur: bool,
    name: str,
) -> Template:
    """Load a template image, run it through the pipeline, precompute its stats."""
    if (filter_spec.width, filter_spec.height) != (width, height):
        raise AssetError(
            f"'{filter_spec.path}' is {filter_spec.width}x{filter_spec.height} but info.xml "
            f"says the {name} region is {width}x{height}. The data folder and info.xml "
            f"disagree; re-download the 'data' folder."
        )

    source = imread_checked(image_path)
    source = fit(source, (width, height))
    mask = white_mask(source)

    if not filter_spec.is_fast_path:
        assert filter_spec.rgba is not None
        rgba_template = _alpha_over(mask, filter_spec.rgba)
        if blur:
            rgba_template = imageops.gaussian_blur(rgba_template, GAUSS_KSIZE)
        return Template(
            name=name,
            width=width,
            height=height,
            blur=blur,
            fast_path=False,
            keep_mask=None,
            centered=None,
            norm=0.0,
            rgba_template=rgba_template,
            filter_rgba=filter_spec.rgba,
        )

    assert filter_spec.keep_mask is not None
    binary = imageops.bitwise_and(mask, filter_spec.keep_mask)
    if blur:
        binary = imageops.gaussian_blur(binary, GAUSS_KSIZE)

    flat = binary.astype(np.float64).ravel()
    centered = flat - flat.mean()
    norm = math.sqrt(float(np.dot(centered, centered)))
    if norm < _MIN_TEMPLATE_NORM:
        # cv2.matchTemplate silently returns 1.0 everywhere for a zero-variance
        # template, which would leave Mercy AI permanently in damage boost with
        # no error and no plausible explanation for the user.
        raise AssetError(
            f"Template '{name}' ({image_path}) has no contrast after filtering -- it is "
            f"blank or fully masked out. Mercy AI would score 1.0 on every frame. "
            f"Re-download the 'data' folder."
        )

    return Template(
        name=name,
        width=width,
        height=height,
        blur=blur,
        fast_path=True,
        keep_mask=filter_spec.keep_mask,
        centered=centered,
        norm=norm,
        rgba_template=None,
        filter_rgba=None,
    )


def fit(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Resize only when the shape actually differs.

    Grabs and templates are normally already the right size, so this is usually
    a no-op; the guard keeps an unexpectedly DPI-scaled grab working instead of
    throwing.
    """
    width, height = size
    if image.shape[1] == width and image.shape[0] == height:
        return image
    return imageops.resize_area(image, size)


def prepare_binary(
    frame: np.ndarray,
    template: Template,
    buffers: MatchBuffers | None = None,
) -> np.ndarray:
    """Run a frame through the fast-path pipeline, returning the single channel.

    Exposed separately so ``--dump-region`` can save each stage.
    """
    if not template.fast_path:
        raise ValueError("prepare_binary is only valid on the fast path.")
    assert template.keep_mask is not None
    if buffers is None:
        mask = white_mask(frame)
        masked = imageops.bitwise_and(mask, template.keep_mask)
        return imageops.gaussian_blur(masked, GAUSS_KSIZE) if template.blur else masked

    white_mask(frame, dst=buffers.mask)
    imageops.bitwise_and(buffers.mask, template.keep_mask, dst=buffers.masked)
    if template.blur:
        imageops.gaussian_blur(buffers.masked, GAUSS_KSIZE, dst=buffers.blurred)
        return buffers.blurred
    return buffers.masked


def ncc_same_size(image: np.ndarray, template: Template) -> float:
    """``TM_CCOEFF_NORMED`` for equally sized single-channel images.

    Reproduces OpenCV's accuracy guard: when the numerator exceeds the
    denominator it is snapped to +/-1 within 12.5 %, and zeroed beyond that.
    """
    assert template.centered is not None
    flat = image.reshape(-1)
    count = flat.size
    total = float(flat.sum())
    sum_sq = float(np.dot(flat, flat))
    # sum((I - mean(I)) * centered) == dot(I, centered), because sum(centered) == 0.
    numerator = float(np.dot(flat, template.centered))
    image_norm = math.sqrt(max(sum_sq - total * total / count, 0.0))

    denominator = image_norm * template.norm
    if abs(numerator) < denominator:
        return numerator / denominator
    if abs(numerator) < denominator * 1.125:
        return 1.0 if numerator > 0 else -1.0
    return 0.0


def _score_general(frame: np.ndarray, template: Template) -> float:
    """Fallback: the general four-channel ``matchTemplate`` path."""
    assert template.filter_rgba is not None and template.rgba_template is not None
    mask = white_mask(frame)
    composite = _alpha_over(mask, template.filter_rgba)
    if template.blur:
        composite = imageops.gaussian_blur(composite, GAUSS_KSIZE)
    return imageops.match_template_max(composite, template.rgba_template)


def score_region(
    frame: np.ndarray,
    template: Template,
    buffers: MatchBuffers | None = None,
) -> float:
    """Score one captured region against its template.

    Returns the correlation in [-1, 1]; higher means a closer match.  Pass
    *buffers* to reuse scratch arrays and avoid per-frame allocation.
    """
    if frame.shape[0] != template.height or frame.shape[1] != template.width:
        frame = fit(frame, (template.width, template.height))
    if not template.fast_path:
        return _score_general(frame, template)

    binary = prepare_binary(frame, template, buffers)
    if buffers is not None:
        np.copyto(buffers.f64, binary, casting="unsafe")
        return ncc_same_size(buffers.f64, template)
    return ncc_same_size(binary.astype(np.float64), template)
