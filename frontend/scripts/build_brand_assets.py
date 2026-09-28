#!/usr/bin/env python3
"""Derive the DEDAN Remote web brand assets from the master artwork.

The master (`dedan remote_logo.png` at the repository root) is a 1774x887
opaque RGB render: a gradient "D" orbital mark, the DEDAN wordmark, a REMOTE
sub-word and a tagline, all sitting on a flat navy plate (#01081a). Shipping it
as-is would paint a visible rectangle over every dark surface in the UI, so
this script cuts the artwork into the three lockups the interface actually
needs and converts the navy plate into real alpha.

Geometry below was measured from the master with a column/row ink profile
rather than guessed, so the crops never clip the mark's outer glow.

Usage:  python3 scripts/build_brand_assets.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from png_codec import decode, encode, resize_rgba  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_ROOT = os.path.dirname(REPO_ROOT)
MASTER = os.path.join(WORKSPACE_ROOT, "dedan remote_logo.png")
OUT_DIR = os.path.join(REPO_ROOT, "public", "brand")

# Background plate colour sampled from the master's corners.
PLATE = (1, 8, 26)
# Channel distance from PLATE at which a pixel becomes fully opaque. Chosen so
# the soft outer glow keeps a smooth feathered edge instead of a hard cut.
FEATHER = 46.0

# Ink boxes measured from the master (x0, y0, x1, y1), padded so the glow is
# never clipped by the crop.
PAD = 6
MARK = (224 - PAD, 234 - PAD, 734 + PAD, 556 + PAD)
LOCKUP_TIGHT = (224 - PAD, 234 - PAD, 1508 + PAD, 556 + PAD)
LOCKUP_FULL = (224 - PAD, 234 - PAD, 1508 + PAD, 646 + PAD)

# Rendered widths. The mark is displayed between 24px and 96px tall, so 200px
# of source width still resolves as a 2x asset on the largest use.
MARK_WIDTH = 200
LOCKUP_WIDTH = 640

# Alpha is collapsed onto 16 levels. The master's alpha channel is a very
# smooth gradient, which is pathological for DEFLATE; quantising it cuts the
# payload by more than half with no visible difference on dark surfaces.
ALPHA_STEPS = 16


def clamp(value: float) -> int:
    if value < 0:
        return 0
    if value > 255:
        return 255
    return int(value)


def key_out_plate(
    pixels: bytes, width: int, height: int, box: tuple[int, int, int, int]
) -> bytes:
    """Crop ``box`` out of an RGB buffer and return an RGBA buffer where the
    navy plate has become transparency.

    Partially transparent pixels are un-premultiplied so the glow keeps its
    saturation once the browser composites it.
    """
    x0, y0, x1, y1 = box
    crop_w, crop_h = x1 - x0, y1 - y0
    out = bytearray(crop_w * crop_h * 4)

    for y in range(y0, y1):
        src_row = y * width
        for x in range(x0, x1):
            o = (src_row + x) * 3
            r, g, b = pixels[o], pixels[o + 1], pixels[o + 2]

            distance = max(abs(r - PLATE[0]), abs(g - PLATE[1]), abs(b - PLATE[2]))
            if distance >= FEATHER:
                alpha = 255
            else:
                alpha = clamp(255 * (1 - distance / FEATHER))
                alpha = (alpha // (256 // ALPHA_STEPS)) * (256 // ALPHA_STEPS)
                alpha = clamp(alpha)

            if 0 < alpha < 255:
                norm = alpha / 255.0
                r = clamp(PLATE[0] + (r - PLATE[0]) / norm)
                g = clamp(PLATE[1] + (g - PLATE[1]) / norm)
                b = clamp(PLATE[2] + (b - PLATE[2]) / norm)

            t = (y - y0) * crop_w * 4 + (x - x0) * 4
            out[t] = r
            out[t + 1] = g
            out[t + 2] = b
            out[t + 3] = alpha

    return bytes(out)


def build(source, box, target_width: int, filename: str) -> None:
    width, height, channels, pixels = source
    if channels != 3:
        raise SystemExit(f"expected an RGB master, found {channels} channels")

    cropped = key_out_plate(pixels, width, height, box)
    crop_w, crop_h = box[2] - box[0], box[3] - box[1]

    if target_width and target_width < crop_w:
        crop_h = max(1, round(crop_h * target_width / crop_w))
        crop_w = target_width
        cropped = resize_rgba(cropped, box[2] - box[0], box[3] - box[1], crop_w, crop_h)

    path = os.path.join(OUT_DIR, filename)
    encode(path, crop_w, crop_h, 4, cropped)
    print(f"  {filename:38s} {crop_w}x{crop_h}  {os.path.getsize(path) / 1024:6.1f} KB")


def main() -> None:
    if not os.path.exists(MASTER):
        raise SystemExit(f"master artwork not found at {MASTER}")

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"master: {MASTER}")
    source = decode(MASTER)

    build(source, MARK, MARK_WIDTH, "dedan-remote-mark.png")
    build(source, LOCKUP_TIGHT, LOCKUP_WIDTH, "dedan-remote-lockup-tight.png")
    build(source, LOCKUP_FULL, LOCKUP_WIDTH, "dedan-remote-lockup.png")
    print("done.")


if __name__ == "__main__":
    main()
