#!/usr/bin/env python3
"""
Logo exploration bench for the Bambu Studio Launcher.

Renders candidate marks side by side at 96/48/32/24/16 on both dark and light
grounds, so a mark is judged at the sizes it will actually be seen rather than
blown up to 256px. Small sizes are where marks live or die.

    python logo_options.py        ->  logo_options.png

The variants below are the round that produced the shipping icon: the bamboo
stalk direction, varied by leaf weight and node treatment. Variant C - a
mid-green stalk with a lighter leaf - won and now lives in make_icon.py.

Keep this file to iterate further (edit OPTIONS and re-run); delete it if the
current mark is settled. Nothing else imports it.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SS = 8

TILE_TOP = (0x27, 0x2C, 0x35)
TILE_BOT = (0x13, 0x15, 0x1A)
EDGE = (0x3A, 0x40, 0x4B)

G_LIGHT = (0x5C, 0xE8, 0x8B)
G_MID = (0x25, 0xC9, 0x5E)
G_DARK = (0x00, 0x8F, 0x38)


# ----------------------------------------------------------------- helpers

def _lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _vgrad(size, top, bottom):
    col = Image.new("RGB", (1, size))
    for y in range(size):
        col.putpixel((0, y), _lerp(top, bottom, y / max(size - 1, 1)))
    return col.resize((size, size), Image.BILINEAR)


def _tile(S):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    radius = int(S * 0.225)
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=255)
    img.paste(_vgrad(S, TILE_TOP, TILE_BOT), (0, 0), m)
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle(
        [0, 0, S - 1, S - 1], radius=radius,
        outline=EDGE + (255,), width=max(SS, int(S * 0.012)))
    return Image.alpha_composite(img, edge)


def _paint(img, S, mask, top=G_LIGHT, bot=G_DARK):
    img.paste(_vgrad(S, top, bot), (0, 0), mask)


def _leaf(d, ox, oy, length, angle_deg, width_ratio=0.30, fill=255, steps=48):
    """A leaf springing from (ox, oy), pointed at both ends."""
    ang = math.radians(angle_deg)
    pts = []
    for side in (1, -1):
        rng = range(steps + 1) if side == 1 else range(steps, -1, -1)
        for i in rng:
            t = i / steps
            w = (length * width_ratio / 2) * (math.sin(math.pi * t) ** 0.72) * side
            x, y = t * length, w
            pts.append((ox + x * math.cos(ang) - y * math.sin(ang),
                        oy + x * math.sin(ang) + y * math.cos(ang)))
    d.polygon(pts, fill=fill)


def _stalk(d, cx, cy, height, width, segments=3, gap_ratio=0.15, fill=255):
    """A vertical stalk of rounded segments separated by node gaps."""
    seg = height / segments
    gap = seg * gap_ratio
    for i in range(segments):
        y0 = cy - height / 2 + seg * i
        d.rounded_rectangle([cx - width / 2, y0 + gap / 2,
                             cx + width / 2, y0 + seg - gap / 2],
                            radius=width * 0.36, fill=fill)


# ------------------------------------------------- refinement round on "F"
#
# F won because the stalk is thick enough to survive downsampling. Everything
# below keeps that and varies only the leaf and the node treatment. The last
# one drops the leaf entirely below 24px, which is the only reliable way to
# keep a small icon from turning to mush.

BASE = dict(cx=0.42, cy=0.50, h=0.62, w=0.235, segs=3, gap=0.12)


def _base_stalk(d, S, **over):
    cfg = {**BASE, **over}
    _stalk(d, S * cfg["cx"], S * cfg["cy"], S * cfg["h"], S * cfg["w"],
           segments=cfg["segs"], gap_ratio=cfg["gap"])
    return S * cfg["cx"], S * cfg["cy"], S * cfg["h"], S * cfg["w"]


def var_a(S, detailed=True):
    """A - F as it stood: thick stalk, one modest leaf."""
    img = _tile(S)
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    cx, cy, H, W = _base_stalk(d, S)
    _leaf(d, cx + W * 0.35, cy - H * 0.24, S * 0.28, -32, width_ratio=0.34)
    _paint(img, S, mask)
    return img


def var_b(S, detailed=True):
    """B - bolder leaf: longer and fatter, so it still exists at 24px."""
    img = _tile(S)
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    cx, cy, H, W = _base_stalk(d, S)
    _leaf(d, cx + W * 0.30, cy - H * 0.20, S * 0.36, -30, width_ratio=0.46)
    _paint(img, S, mask)
    return img


def var_c(S, detailed=True):
    """C - two tones: mid-green stalk, light-green leaf, for separation."""
    img = _tile(S)
    stalk = Image.new("L", (S, S), 0)
    ds = ImageDraw.Draw(stalk)
    cx, cy, H, W = _base_stalk(ds, S)
    leaf = Image.new("L", (S, S), 0)
    _leaf(ImageDraw.Draw(leaf), cx + W * 0.30, cy - H * 0.20, S * 0.36, -30,
          width_ratio=0.46)
    _paint(img, S, stalk, G_MID, G_DARK)
    _paint(img, S, leaf, (0x7C, 0xF0, 0xA4), G_MID)
    return img


def var_d(S, detailed=True):
    """D - node collars: a wider band at each joint, the way real bamboo looks."""
    img = _tile(S)
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    cx, cy, H, W = _base_stalk(d, S)
    if detailed:
        seg = H / BASE["segs"]
        for i in range(1, BASE["segs"]):
            y = cy - H / 2 + seg * i
            d.rounded_rectangle([cx - W * 0.62, y - W * 0.085,
                                 cx + W * 0.62, y + W * 0.085],
                                radius=W * 0.085, fill=255)
    _leaf(d, cx + W * 0.30, cy - H * 0.20, S * 0.36, -30, width_ratio=0.46)
    _paint(img, S, mask)
    return img


def var_e(S, detailed=True):
    """E - two leaves, both sweeping up, tucked close to the stalk."""
    img = _tile(S)
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    cx, cy, H, W = _base_stalk(d, S)
    _leaf(d, cx + W * 0.30, cy - H * 0.22, S * 0.34, -34, width_ratio=0.44)
    if detailed:
        _leaf(d, cx - W * 0.30, cy - H * 0.02, S * 0.24, -148, width_ratio=0.44)
    _paint(img, S, mask)
    return img


def var_f(S, detailed=True):
    """F - adaptive: below 24px the leaf is dropped and the stalk grows.

    A segmented stalk on its own still reads as bamboo; a 3px leaf reads as
    dirt on the screen.
    """
    img = _tile(S)
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    if detailed:
        cx, cy, H, W = _base_stalk(d, S)
        _leaf(d, cx + W * 0.30, cy - H * 0.20, S * 0.36, -30, width_ratio=0.46)
    else:
        _base_stalk(d, S, cx=0.5, w=0.30, h=0.66)
    _paint(img, S, mask)
    return img


OPTIONS = [
    ("A  modest leaf", var_a),
    ("B  bolder leaf", var_b),
    ("C  two-tone", var_c),
    ("D  node collars", var_d),
    ("E  two leaves", var_e),
    ("F  adaptive small", var_f),
]


# ------------------------------------------------------------ contact sheet

def render(fn, px):
    # Variants may simplify themselves when the target is tiny.
    return fn(px * SS, detailed=px >= 24).resize((px, px), Image.LANCZOS)


def main() -> None:
    try:
        font = ImageFont.truetype("segoeuib.ttf", 15)
    except OSError:
        font = ImageFont.load_default()

    sizes = [96, 48, 32, 24, 16]
    row_h = 128
    pad = 22
    label_w = 175
    light_x = label_w + sum(sizes) + pad * len(sizes) + 30

    W = light_x + 96 + 40
    H = row_h * len(OPTIONS) + 40
    sheet = Image.new("RGB", (W, H), (0x16, 0x18, 0x1C))
    sheet.paste(Image.new("RGB", (96 + 30, H), (0xF1, 0xF2, 0xF4)), (light_x - 15, 0))
    d = ImageDraw.Draw(sheet)

    y = 20
    for name, fn in OPTIONS:
        d.text((18, y + row_h // 2 - 8), name, font=font, fill=(0xE8, 0xEA, 0xEE))
        x = label_w
        for px in sizes:
            im = render(fn, px)
            sheet.paste(im, (x, y + (row_h - px) // 2), im)
            x += px + pad
        im = render(fn, 48)
        sheet.paste(im, (light_x + 24, y + (row_h - 48) // 2), im)
        im = render(fn, 20)
        sheet.paste(im, (light_x + 24, y + (row_h - 48) // 2 + 56), im)
        y += row_h

    out = HERE / "logo_options.png"
    sheet.save(out)
    print("wrote {} ({} variants)".format(out.name, len(OPTIONS)))


if __name__ == "__main__":
    main()
