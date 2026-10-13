"""Tray icon: a 4-point north star on deep navy, drawn with Pillow at runtime."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

NAVY = (14, 26, 58, 255)
STAR = {
    "gold": (242, 190, 60, 255),
    "grey": (150, 156, 168, 255),
    "red": (226, 72, 72, 255),
}


@lru_cache(maxsize=None)
def make_icon(color: str = "gold", size: int = 64) -> Image.Image:
    big = size * 4  # supersample for smooth edges
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = big // 5
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=radius, fill=NAVY)
    c = big / 2
    outer = big * 0.42
    inner = big * 0.11
    points = [
        (c, c - outer), (c + inner, c - inner),
        (c + outer, c), (c + inner, c + inner),
        (c, c + outer), (c - inner, c + inner),
        (c - outer, c), (c - inner, c - inner),
    ]
    draw.polygon(points, fill=STAR.get(color, STAR["grey"]))
    dot = big * 0.035
    draw.ellipse((c - dot, c - dot, c + dot, c + dot), fill=NAVY)
    return img.resize((size, size), Image.LANCZOS)


def write_ico(path: Path, color: str = "gold") -> Path:
    """Multi-size .ico for PyInstaller / shortcuts."""

    path.parent.mkdir(parents=True, exist_ok=True)
    make_icon(color, 256).save(path, format="ICO",
                               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path
