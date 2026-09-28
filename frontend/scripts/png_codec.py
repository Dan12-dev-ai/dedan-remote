"""Minimal dependency-free PNG codec (8-bit, non-interlaced, stdlib only).

The brand assets are derived from a single master artwork, so the build step
needs to decode, crop, colour-key and resize without pulling a heavyweight
image library into a browser-only project. Only ``zlib`` and ``struct`` are
used, keeping the toolchain installable on any machine.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# IHDR colour-type -> number of samples per pixel.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
# Sample count -> IHDR colour-type, for writing.
_COLOUR_TYPE = {1: 0, 2: 4, 3: 2, 4: 6}

_SUPPORTED_DEPTH = 8


def _iter_chunks(data: bytes):
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("not a PNG file")
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        chunk_type = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        yield chunk_type, payload
        offset += 12 + length


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode(path: str) -> tuple[int, int, int, bytes]:
    """Return ``(width, height, channels, pixels)`` for an 8-bit PNG."""
    ihdr = None
    compressed = bytearray()

    with open(path, "rb") as handle:
        raw_file = handle.read()

    for chunk_type, payload in _iter_chunks(raw_file):
        if chunk_type == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", payload)
        elif chunk_type == b"IDAT":
            compressed += payload

    if ihdr is None:
        raise ValueError("PNG has no IHDR chunk")

    width, height, depth, colour_type, _comp, _filter, interlace = ihdr
    if depth != _SUPPORTED_DEPTH or interlace != 0:
        raise ValueError(f"unsupported PNG format: {ihdr}")

    channels = _CHANNELS[colour_type]
    stride = width * channels
    raw = zlib.decompress(bytes(compressed))

    out = bytearray(height * stride)
    cursor = 0
    for y in range(height):
        filter_type = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor : cursor + stride])
        cursor += stride

        base = y * stride
        previous = out[base - stride : base] if y else bytes(stride)

        if filter_type == 0:
            pass
        elif filter_type == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif filter_type == 2:
            for x in range(stride):
                line[x] = (line[x] + previous[x]) & 0xFF
        elif filter_type == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + previous[x]) >> 1)) & 0xFF
        elif filter_type == 4:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                upper_left = previous[x - channels] if x >= channels else 0
                line[x] = (line[x] + _paeth(left, previous[x], upper_left)) & 0xFF
        else:
            raise ValueError(f"unknown scanline filter {filter_type}")

        out[base : base + stride] = line

    return width, height, channels, bytes(out)


def encode(path: str, width: int, height: int, channels: int, pixels: bytes) -> None:
    """Write an 8-bit PNG. ``channels`` is 1 (grey), 2 (grey+alpha),
    3 (RGB) or 4 (RGBA)."""
    colour_type = _COLOUR_TYPE[channels]
    stride = width * channels

    filtered = bytearray()
    for y in range(height):
        filtered.append(0)  # filter type 0 (None) keeps the encoder simple
        filtered += pixels[y * stride : (y + 1) * stride]

    def chunk(chunk_type: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        return (
            struct.pack(">I", len(payload))
            + chunk_type
            + payload
            + struct.pack(">I", checksum)
        )

    output = PNG_SIGNATURE
    output += chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    )
    output += chunk(b"IDAT", zlib.compress(bytes(filtered), 9))
    output += chunk(b"IEND", b"")

    with open(path, "wb") as handle:
        handle.write(output)


def resize_rgba(
    src: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int
) -> bytes:
    """Box-filter downscale for RGBA buffers (averages every source pixel that
    falls inside a destination cell, so glows stay smooth)."""
    out = bytearray(dst_w * dst_h * 4)
    x_ratio = src_w / dst_w
    y_ratio = src_h / dst_h

    for dy in range(dst_h):
        y0 = int(dy * y_ratio)
        y1 = max(y0 + 1, int((dy + 1) * y_ratio))
        for dx in range(dst_w):
            x0 = int(dx * x_ratio)
            x1 = max(x0 + 1, int((dx + 1) * x_ratio))
            r = g = b = a = 0
            count = 0
            for sy in range(y0, min(y1, src_h)):
                row = sy * src_w
                for sx in range(x0, min(x1, src_w)):
                    o = (row + sx) * 4
                    r += src[o]
                    g += src[o + 1]
                    b += src[o + 2]
                    a += src[o + 3]
                    count += 1
            t = (dy * dst_w + dx) * 4
            out[t] = r // count
            out[t + 1] = g // count
            out[t + 2] = b // count
            out[t + 3] = a // count

    return bytes(out)
