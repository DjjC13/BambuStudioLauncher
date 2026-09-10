#!/usr/bin/env python3
"""
Generates icon.ico / icon.png for the Bambu Studio Launcher.

The mark: a bamboo shoot - a thick segmented stalk in mid green with a lighter
leaf sweeping up from it, on a dark rounded tile. Bambu Lab's green and their
flat geometric language, without copying their actual logo.

Two things make it survive being shrunk:

* The stalk is deliberately thick. Thin strokes and small leaves are what
  turned the earlier attempts to mush by 24px.
* The leaf is a lighter tone than the stalk. Two shapes in one flat colour
  merge into a blob when downsampled; a tonal step keeps them separate.
* Below 24px the leaf is dropped entirely and the stalk grows to fill the
  tile. A three-pixel leaf is not a leaf, it is dirt on the screen - and a
  segmented stalk on its own still reads as bamboo.

Everything is drawn at 8x and downsampled, which is how you get smooth curves
out of Pillow.

Run:  python make_icon.py     (needs Pillow; not required at runtime)
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SS = 8  # supersampling factor

TILE_TOP = (0x27, 0x2C, 0x35)
TILE_BOT = (0x13, 0x15, 0x1A)
EDGE = (0x3A, 0x40, 0x4B)

# Bambu Lab's green, in three steps.
G_LEAF_TOP = (0x7C, 0xF0, 0xA4)
G_MID = (0x25, 0xC9, 0x5E)
G_DARK = (0x00, 0x8F, 0x38)

# Geometry, all as fractions of the icon size.
#
# Segments must be clearly taller than they are wide, with a small corner
# radius and a tight node gap. Square-ish segments with a big radius read as
# three stacked pills, not as one stalk - that was the first attempt's flaw.
STALK = dict(cx=0.385, cy=0.575, h=0.545, w=0.185, segments=3, gap=0.095,
             radius=0.26)
STALK_SMALL = dict(cx=0.50, cy=0.50, h=0.72, w=0.255, segments=3, gap=0.095,
                   radius=0.26)
LEAF = dict(length=0.30, angle=-30, width_ratio=0.44)
# Leaf origin, as fractions of the stalk width / height from its centre. It sits
# just clear of the stalk's top-right corner: a leaf whose pointed base lands on
# the stalk fuses with the top segment and the pair reads as a hook.
LEAF_ORIGIN = (0.60, -0.44)

SIMPLIFY_BELOW = 24  # px


def _lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _vgrad(size, top, bottom):
    col = Image.new("RGB", (1, size))
    for y in range(size):
        col.putpixel((0, y), _lerp(top, bottom, y / max(size - 1, 1)))
    return col.resize((size, size), Image.BILINEAR)


def _tile(S: int) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    radius = int(S * 0.225)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, S - 1, S - 1], radius=radius, fill=255)
    img.paste(_vgrad(S, TILE_TOP, TILE_BOT), (0, 0), mask)

    # A hairline edge stops the tile reading as a flat blob on dark backgrounds.
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle(
        [0, 0, S - 1, S - 1], radius=radius,
        outline=EDGE + (255,), width=max(SS, int(S * 0.012)))
    return Image.alpha_composite(img, edge)


def _draw_stalk(d, S: int, cfg: dict):
    """Rounded segments separated by node gaps. Returns the stalk's box."""
    cx, cy = S * cfg["cx"], S * cfg["cy"]
    H, W = S * cfg["h"], S * cfg["w"]
    seg = H / cfg["segments"]
    gap = seg * cfg["gap"]
    for i in range(cfg["segments"]):
        y0 = cy - H / 2 + seg * i
        d.rounded_rectangle([cx - W / 2, y0 + gap / 2, cx + W / 2, y0 + seg - gap / 2],
                            radius=W * cfg["radius"], fill=255)
    return cx, cy, H, W


def _draw_leaf(d, S: int, ox: float, oy: float):
    """A leaf pointed at both ends, springing from (ox, oy)."""
    ang = math.radians(LEAF["angle"])
    length = S * LEAF["length"]
    pts = []
    steps = 64
    for side in (1, -1):
        rng = range(steps + 1) if side == 1 else range(steps, -1, -1)
        for i in rng:
            t = i / steps
            w = (length * LEAF["width_ratio"] / 2) * (math.sin(math.pi * t) ** 0.72) * side
            x, y = t * length, w
            pts.append((ox + x * math.cos(ang) - y * math.sin(ang),
                        oy + x * math.sin(ang) + y * math.cos(ang)))
    d.polygon(pts, fill=255)


def render(px: int) -> Image.Image:
    S = px * SS
    img = _tile(S)
    detailed = px >= SIMPLIFY_BELOW

    stalk = Image.new("L", (S, S), 0)
    cx, cy, H, W = _draw_stalk(ImageDraw.Draw(stalk), S,
                              STALK if detailed else STALK_SMALL)

    if detailed:
        # Stalk in mid green, leaf a step lighter so the two never merge.
        img.paste(_vgrad(S, G_MID, G_DARK), (0, 0), stalk)
        # The leaf springs from the crown, growing up and to the right.
        leaf = Image.new("L", (S, S), 0)
        _draw_leaf(ImageDraw.Draw(leaf), S,
                   cx + W * LEAF_ORIGIN[0], cy + H * LEAF_ORIGIN[1])
        img.paste(_vgrad(S, G_LEAF_TOP, G_MID), (0, 0), leaf)
    else:
        # No leaf to contrast against, so run the stalk brighter for contrast
        # against the dark tile at these sizes.
        img.paste(_vgrad(S, G_LEAF_TOP, G_MID), (0, 0), stalk)

    return img.resize((px, px), Image.LANCZOS)


def main() -> None:
    sizes = [16, 20, 24, 32, 48, 64, 128, 256]
    frames = [render(s) for s in sizes]

    ico = HERE / "icon.ico"
    frames[-1].save(ico, format="ICO",
                    sizes=[(s, s) for s in sizes], append_images=frames[:-1])

    # PNG for the in-app header (tkinter reads PNG natively on Tk 8.6).
    render(256).save(HERE / "icon.png")
    render(64).save(HERE / "icon_64.png")

    print("wrote {} ({:,} bytes) covering {}".format(
        ico.name, ico.stat().st_size, ", ".join(str(s) for s in sizes)))
    print("wrote icon.png, icon_64.png")
    print("leaf dropped below {}px".format(SIMPLIFY_BELOW))


if __name__ == "__main__":
    main()
