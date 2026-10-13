"""Draw element boxes and ref numbers on a screenshot and save it under backend/data/shots/."""
from __future__ import annotations

import io
import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

SHOTS_DIR = Path(__file__).resolve().parents[1] / "data" / "shots"
_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_BOX_COLOR = (230, 30, 60)
_LABEL_TEXT = (255, 255, 255)


def shot_name(request_id: str, n: int) -> str:
    return f"{_SAFE.sub('_', request_id)[:64] or 'req'}_{int(n)}.png"


def _font(size: int = 12) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def annotate(png: bytes, elements: list[dict], request_id: str, n: int) -> str:
    """Save an annotated copy of png and return its public path, e.g. /shots/r_ab12_2.png."""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _font()

    for el in elements:
        x, y, w, h = el["bbox"]
        draw.rectangle([x, y, x + w, y + h], outline=_BOX_COLOR, width=1)
        label = str(el["ref"])
        lx0, ly0, lx1, ly1 = draw.textbbox((0, 0), label, font=font)
        lw, lh = lx1 - lx0 + 4, ly1 - ly0 + 2
        top = y - lh if y - lh >= 0 else y
        draw.rectangle([x, top, x + lw, top + lh], fill=_BOX_COLOR)
        draw.text((x + 2 - lx0, top + 1 - ly0), label, fill=_LABEL_TEXT, font=font)

    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    name = shot_name(request_id, n)
    image.save(SHOTS_DIR / name, format="PNG", optimize=True)
    return f"/shots/{name}"
