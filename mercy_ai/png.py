# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 Hexer-7
"""A PNG reader and writer built on ``zlib`` alone.

The only reason Mercy AI needs an image *codec* is that its eight template
assets are stored as PNG.  Everything else it does to a picture is arithmetic on
a numpy array.  Carrying OpenCV (about 71 MB) just to decode 19 KB of PNG at
startup is a poor trade, so builds without OpenCV use this module instead.

Deliberately narrow.  It handles exactly the files this project ships: 8 bits
per channel, colour type 2 (RGB) or 6 (RGBA), no interlacing, no palette, no
16-bit samples.  Anything else raises rather than guessing, because a
half-supported decoder that returns subtly wrong pixels would be far worse here
than one that refuses: the pixels *are* the matching template.

Byte order is OpenCV's, not PNG's.  :func:`decode` returns BGR/BGRA so that a
caller can swap this module in for ``cv2.imread`` without every downstream index
changing meaning.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

__all__ = ["PngError", "decode", "encode"]

_MAGIC = b"\x89PNG\r\n\x1a\n"

#: colour type -> channels.  2 = truecolour, 6 = truecolour with alpha.
_CHANNELS = {2: 3, 6: 4}


class PngError(ValueError):
    """The file is not a PNG this module is prepared to read."""


def _chunks(data: bytes):
    offset = len(_MAGIC)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        yield kind, body
        offset += 12 + length  # length + type + body + crc


def _unfilter(raw: bytes, height: int, stride: int, step: int) -> np.ndarray:
    """Reverse the per-scanline filters defined in the PNG specification.

    Sub, Average and Paeth each refer to the pixel to the *left*, so a row
    cannot be vectorised: byte ``i`` depends on byte ``i - step`` of the same
    row, which was itself just reconstructed.  The rows that use them are walked
    in Python, which is affordable because this runs once, at startup, over
    images no larger than 184x156.  ``None`` and ``Up`` have no such dependency
    and take the fast path.
    """
    out = np.zeros((height, stride), dtype=np.uint8)
    position = 0
    previous = np.zeros(stride, dtype=np.uint8)
    for row in range(height):
        kind = raw[position]
        line = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=position + 1).copy()
        position += 1 + stride
        if kind == 0:  # None
            pass
        elif kind == 2:  # Up
            line += previous
        elif kind == 1:  # Sub
            for i in range(step, stride):
                line[i] = (int(line[i]) + int(line[i - step])) & 0xFF
        elif kind == 3:  # Average
            for i in range(stride):
                left = int(line[i - step]) if i >= step else 0
                line[i] = (int(line[i]) + ((left + int(previous[i])) >> 1)) & 0xFF
        elif kind == 4:  # Paeth
            for i in range(stride):
                left = int(line[i - step]) if i >= step else 0
                up = int(previous[i])
                upper_left = int(previous[i - step]) if i >= step else 0
                estimate = left + up - upper_left
                da, db, dc = (
                    abs(estimate - left),
                    abs(estimate - up),
                    abs(estimate - upper_left),
                )
                nearest = left if (da <= db and da <= dc) else (up if db <= dc else upper_left)
                line[i] = (int(line[i]) + nearest) & 0xFF
        else:
            raise PngError(f"Unknown PNG row filter {kind}.")
        out[row] = line
        previous = line
    return out


def decode(data: bytes) -> np.ndarray:
    """Decode *data* into an ``HxWx3`` BGR or ``HxWx4`` BGRA ``uint8`` array."""
    if not data.startswith(_MAGIC):
        raise PngError("Not a PNG file (bad signature).")
    header = None
    idat = bytearray()
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body[:13])
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if header is None:
        raise PngError("PNG has no IHDR chunk.")
    width, height, depth, colour, compression, filters, interlace = header
    if depth != 8:
        raise PngError(f"Only 8-bit PNGs are supported, this one is {depth}-bit.")
    if colour not in _CHANNELS:
        raise PngError(f"Unsupported PNG colour type {colour}; expected 2 (RGB) or 6 (RGBA).")
    if compression != 0 or filters != 0:
        raise PngError("Unsupported PNG compression or filter method.")
    if interlace != 0:
        raise PngError("Interlaced PNGs are not supported.")
    if not idat:
        raise PngError("PNG has no image data.")

    channels = _CHANNELS[colour]
    stride = width * channels
    raw = zlib.decompress(bytes(idat))
    expected = height * (stride + 1)
    if len(raw) < expected:
        raise PngError(f"PNG data is truncated ({len(raw)} bytes, expected {expected}).")

    pixels = _unfilter(raw, height, stride, channels).reshape(height, width, channels)
    # RGB(A) on disk, BGR(A) in memory: match cv2.imread so callers need no
    # knowledge of which backend decoded the file.
    if channels == 3:
        return pixels[:, :, ::-1].copy()
    return np.concatenate((pixels[:, :, 2::-1], pixels[:, :, 3:]), axis=2)


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def encode(image: np.ndarray) -> bytes:
    """Encode a BGR or BGRA ``uint8`` array as a PNG.

    Every row is written with filter ``None``.  The output is a few per cent
    larger than an optimising encoder would produce, which is irrelevant: the
    only writer is ``--dump-region``, a diagnostic that saves a handful of small
    images so a user can see what Mercy AI sees.
    """
    if image.dtype != np.uint8:
        raise PngError(f"Only uint8 images can be written, got {image.dtype}.")
    if image.ndim == 2:
        image = np.repeat(image[:, :, None], 3, axis=2)
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise PngError(f"Expected a 2-D, HxWx3 or HxWx4 image, got shape {image.shape}.")

    height, width, channels = image.shape
    rgb = (
        image[:, :, ::-1]
        if channels == 3
        else np.concatenate((image[:, :, 2::-1], image[:, :, 3:]), axis=2)
    )
    body = np.zeros((height, width * channels + 1), dtype=np.uint8)
    body[:, 1:] = rgb.reshape(height, -1)
    colour = 2 if channels == 3 else 6
    return b"".join(
        (
            _MAGIC,
            _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0)),
            _chunk(b"IDAT", zlib.compress(body.tobytes(), 6)),
            _chunk(b"IEND", b""),
        )
    )
