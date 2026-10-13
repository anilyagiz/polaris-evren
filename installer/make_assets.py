"""Kurulum sihirbazi gorselleri: polaris.ico + wizard-*.bmp (Pillow).

Kullanim: python installer/make_assets.py <icon-128.png> <cikti-klasoru>
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

NAVY = (11, 16, 32)
NAVY_2 = (27, 37, 80)
GOLD = (242, 200, 121)
IVORY = (251, 248, 241)


def _icon(src: Path, size: int) -> Image.Image:
    img = Image.open(src).convert("RGBA")
    return img.resize((size, size), Image.LANCZOS)


def make_ico(src: Path, out: Path) -> None:
    base = _icon(src, 256)
    base.save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def make_large(src: Path, out: Path, scale: int) -> None:
    w, h = 164 * scale, 314 * scale
    img = Image.new("RGB", (w, h), NAVY)
    draw = ImageDraw.Draw(img)
    for y in range(h):  # yukaridan asagi lacivert gecis
        t = y / h
        c = tuple(int(NAVY_2[i] * (1 - t) + NAVY[i] * t) for i in range(3))
        draw.line([(0, y), (w, y)], fill=c)
    for x, y, r in [(22, 40, 1), (130, 70, 1), (40, 250, 1), (140, 220, 1), (100, 290, 1), (20, 160, 1)]:
        draw.ellipse([x * scale - r * scale, y * scale - r * scale, x * scale + r * scale, y * scale + r * scale], fill=GOLD)
    icon = _icon(src, 96 * scale)
    img.paste(icon, ((w - icon.width) // 2, 92 * scale), icon)
    draw.line([(52 * scale, 212 * scale), (112 * scale, 212 * scale)], fill=GOLD, width=max(1, scale))
    img.save(out, format="BMP")


def make_small(src: Path, out: Path, size: int) -> None:
    img = Image.new("RGB", (size, size), IVORY)
    icon = _icon(src, int(size * 0.9))
    img.paste(icon, ((size - icon.width) // 2, (size - icon.height) // 2), icon)
    img.save(out, format="BMP")


def main() -> int:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    make_ico(src, out / "polaris.ico")
    make_large(src, out / "wizard-large.bmp", 1)
    make_large(src, out / "wizard-large-2x.bmp", 2)
    make_small(src, out / "wizard-small.bmp", 55)
    make_small(src, out / "wizard-small-2x.bmp", 110)
    print("assets:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
