# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""The two image backends, and the PNG codec that lets one of them exist.

Mercy AI.exe is built with OpenCV removed -- 71 MB of a 129 MB
download, for a colour threshold, a bitwise AND, a blur and a PNG decoder.  The
question these tests exist to answer is not "does the numpy code run" but **"does
it produce the same answers"**, because the thing being replaced is the part that
decides whether to heal or to damage boost.

So the numpy backend is measured against OpenCV on the *real* shipped assets, and
the agreement is asserted as a number rather than assumed.
"""

from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager

import numpy as np
import pytest

from mercy_ai import imageops
from mercy_ai.png import PngError, decode, encode

cv2 = pytest.importorskip("cv2", reason="the comparison needs the reference implementation")


@contextmanager
def numpy_backend():
    """Reload :mod:`mercy_ai.imageops` as if OpenCV were not installed.

    ``importlib.reload`` mutates the existing module object rather than making a
    new one, so ``vision.py``'s ``from . import imageops`` keeps pointing at the
    same module and picks up the swap for free -- which is what makes the
    end-to-end comparison below possible without a second interpreter.
    """

    class Blocker:
        def find_module(self, name, path=None):  # pragma: no cover - legacy hook
            return None

        def find_spec(self, name, path=None, target=None):
            if name == "cv2":
                raise ImportError("cv2 is hidden for this test")

    saved = sys.modules.pop("cv2", None)
    blocker = Blocker()
    sys.meta_path.insert(0, blocker)
    try:
        importlib.reload(imageops)
        assert imageops.BACKEND == "numpy"
        yield imageops
    finally:
        sys.meta_path.remove(blocker)
        if saved is not None:
            sys.modules["cv2"] = saved
        importlib.reload(imageops)
        assert imageops.BACKEND == "opencv"


# --------------------------------------------------------------------------- #
# The PNG codec
# --------------------------------------------------------------------------- #


def test_every_shipped_asset_decodes_exactly_as_opencv_reads_it(data_dir):
    """These pixels *are* the matching template; "close enough" is not enough."""
    files = sorted(data_dir.rglob("*.png"))
    assert files, "no assets found"
    for path in files:
        mine = decode(path.read_bytes())
        reference = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        assert mine.shape == reference.shape, path
        assert np.array_equal(mine, reference), path


@pytest.mark.parametrize("channels", [3, 4])
def test_writing_and_reading_a_png_round_trips(channels, rng):
    image = rng.integers(0, 256, (37, 53, channels), dtype=np.uint8)
    assert np.array_equal(decode(encode(image)), image)


@pytest.mark.parametrize("channels", [3, 4])
def test_opencv_can_read_what_we_write(channels, rng):
    """A dumped diagnostic image has to open in something other than this program."""
    image = rng.integers(0, 256, (11, 17, channels), dtype=np.uint8)
    blob = np.frombuffer(encode(image), np.uint8)
    assert np.array_equal(cv2.imdecode(blob, cv2.IMREAD_UNCHANGED), image)


def test_a_file_that_is_not_a_png_is_refused_rather_than_guessed_at():
    with pytest.raises(PngError, match="signature"):
        decode(b"this is not a png")


def test_an_unsupported_png_flavour_is_refused(rng):
    """A half-supported decoder returning wrong pixels is worse than one that stops."""
    sixteen_bit = cv2.imencode(".png", rng.integers(0, 65535, (8, 8, 3), dtype=np.uint16))[1]
    with pytest.raises(PngError):
        decode(sixteen_bit.tobytes())


def test_a_truncated_png_is_refused(data_dir):
    blob = next(data_dir.rglob("*.png")).read_bytes()
    with pytest.raises((PngError, Exception)):
        decode(blob[: len(blob) // 2])


# --------------------------------------------------------------------------- #
# The backends, side by side
# --------------------------------------------------------------------------- #


def test_the_backend_names_itself():
    assert imageops.BACKEND in ("opencv", "numpy")
    assert imageops.HAVE_OPENCV is (imageops.BACKEND == "opencv")


def test_the_colour_threshold_is_bit_identical(rng):
    from mercy_ai.vision import LOWER_BGRA, UPPER_BGRA

    frames = [rng.integers(0, 256, (40, 60, 4), dtype=np.uint8) for _ in range(5)]
    reference = [cv2.inRange(f, LOWER_BGRA, UPPER_BGRA) for f in frames]
    with numpy_backend() as ops:
        for frame, expected in zip(frames, reference, strict=True):
            assert np.array_equal(ops.in_range(frame, LOWER_BGRA, UPPER_BGRA), expected)


def test_the_bitwise_and_is_bit_identical(rng):
    a = rng.integers(0, 256, (40, 60), dtype=np.uint8)
    b = rng.integers(0, 256, (40, 60), dtype=np.uint8)
    expected = cv2.bitwise_and(a, b)
    with numpy_backend() as ops:
        assert np.array_equal(ops.bitwise_and(a, b), expected)


def test_writing_into_a_preallocated_buffer_gives_the_same_answer(rng):
    """The hot path reuses buffers; the `dst=` route must not diverge."""
    from mercy_ai.vision import LOWER_BGRA, UPPER_BGRA

    frame = rng.integers(0, 256, (30, 40, 4), dtype=np.uint8)
    with numpy_backend() as ops:
        fresh = ops.in_range(frame, LOWER_BGRA, UPPER_BGRA)
        buffer = np.empty((30, 40), dtype=np.uint8)
        ops.in_range(frame, LOWER_BGRA, UPPER_BGRA, dst=buffer)
        assert np.array_equal(fresh, buffer)


#: Measured, not hoped for. OpenCV's own 41-tap path is an approximation; raising
#: this implementation's precision moves it away from cv2 rather than towards it.
BLUR_PIXEL_TOLERANCE = 4


@pytest.mark.parametrize("size", [(88, 80), (120, 102), (184, 156), (16, 16)])
def test_the_blur_matches_opencv_to_within_a_measured_bound(size, rng):
    from mercy_ai.vision import GAUSS_KSIZE

    width, height = size
    for _ in range(4):
        image = (rng.integers(0, 2, (height, width)) * 255).astype(np.uint8)
        expected = cv2.GaussianBlur(image, GAUSS_KSIZE, 0)
        with numpy_backend() as ops:
            mine = ops.gaussian_blur(image, GAUSS_KSIZE)
        assert mine.shape == expected.shape and mine.dtype == expected.dtype
        worst = int(np.abs(mine.astype(int) - expected.astype(int)).max())
        assert worst <= BLUR_PIXEL_TOLERANCE, f"{size}: off by {worst}/255"


def test_the_blur_preserves_a_flat_image(rng):
    """Unity gain. A blur that brightens or darkens would bias every score."""
    from mercy_ai.vision import GAUSS_KSIZE

    flat = np.full((60, 70), 200, dtype=np.uint8)
    with numpy_backend() as ops:
        assert np.array_equal(ops.gaussian_blur(flat, GAUSS_KSIZE), flat)


def test_area_resize_averages_rather_than_samples():
    with numpy_backend() as ops:
        image = np.array([[0, 0, 255, 255], [0, 0, 255, 255]], dtype=np.uint8)
        halved = ops.resize_area(image, (2, 1))
        assert halved.shape == (1, 2)
        assert halved.tolist() == [[0, 255]]


def test_matching_equally_sized_images_agrees_with_opencv(rng):
    for _ in range(5):
        a = rng.integers(0, 256, (20, 24, 4), dtype=np.uint8)
        b = rng.integers(0, 256, (20, 24, 4), dtype=np.uint8)
        expected = float(cv2.minMaxLoc(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED))[1])
        with numpy_backend() as ops:
            assert ops.match_template_max(a, b) == pytest.approx(expected, abs=1e-6)


def test_a_sliding_search_says_so_instead_of_returning_a_wrong_number(rng):
    """`fit` has already matched the sizes, so this cannot happen in the program."""
    with numpy_backend() as ops:
        with pytest.raises(ValueError, match="equally sized"):
            ops.match_template_max(
                rng.integers(0, 256, (20, 24, 4), dtype=np.uint8),
                rng.integers(0, 256, (8, 8, 4), dtype=np.uint8),
            )


# --------------------------------------------------------------------------- #
# What actually matters: the score
# --------------------------------------------------------------------------- #

#: The number that decides heal versus damage boost. The dial spans 0.20 to 0.99
#: over ten steps, so one step is 0.088 -- this is two orders of magnitude below
#: the smallest adjustment a user can even make.
SCORE_TOLERANCE = 3e-3


@pytest.mark.parametrize("resolution", ["2160", "1440", "1080", "768"])
def test_the_two_backends_score_the_same_frames_the_same_way(resolution, rng):
    """The end-to-end check: identical assets, identical frames, both pipelines."""
    from mercy_ai.assets import load_assets
    from mercy_ai.vision import score_region

    frames = []
    assets = load_assets(resolution)
    for name in ("health", "bomb"):
        template = getattr(assets, name)
        for _ in range(8):
            frame = rng.integers(0, 90, (template.height, template.width, 4), dtype=np.uint8)
            fill = int(rng.integers(1, template.width + 1))
            frame[:, :fill, :3] = rng.integers(235, 256, (template.height, fill, 3), dtype=np.uint8)
            frames.append((name, frame))

    reference = [score_region(f, getattr(assets, n), None) for n, f in frames]
    with numpy_backend():
        numpy_assets = load_assets(resolution)
        mine = [score_region(f, getattr(numpy_assets, n), None) for n, f in frames]

    worst = max(abs(a - b) for a, b in zip(reference, mine, strict=True))
    assert worst < SCORE_TOLERANCE, f"{resolution}: scores differ by {worst:.2e}"


def test_the_numpy_backend_loads_the_real_assets_without_opencv():
    """If the templates cannot be built, nothing else about the numpy backend matters."""
    from mercy_ai.assets import load_assets

    with numpy_backend():
        assets = load_assets("1080")
    assert assets.health.fast_path and assets.health.blur
    assert assets.health.centered is not None and assets.health.norm > 0
    assert assets.bomb.keep_mask is not None
