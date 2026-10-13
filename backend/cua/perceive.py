"""Screen perception: screenshot PNG -> numbered element list (Snapshot) the text-only LLM can read.

DESKTOP_PARSER=ocr (default): rapidocr-onnxruntime text boxes, merged per line.
DESKTOP_PARSER=omniparser: not implemented yet, falls back to OCR.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from typing import Any

from backend.cua.annotate import annotate

logger = logging.getLogger(__name__)

MAX_ELEMENTS = 150
MAX_NAME_CHARS = 80
EXCERPT_CHARS = 1500
MIN_SCORE = 0.5
LINE_OVERLAP = 0.5   # vertical overlap (fraction of the smaller box) to count as the same line
WORD_GAP = 0.6       # max horizontal gap, in box heights, to merge boxes into one element
SPLIT_GAP = 0.8      # a gap this wide (in box heights) between characters splits one OCR box (menu items)
CHAR_PAD = 2         # px added around boxes built from character boxes

_engine: Any = None


def _get_engine() -> Any:
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        started = time.perf_counter()
        _engine = RapidOCR()
        logger.info("RapidOCR loaded in %.1fs", time.perf_counter() - started)
    return _engine


def _ocr_boxes(png: bytes) -> list[dict]:
    """Run OCR and return [{"text", "bbox": [x, y, w, h], "score"}]."""
    import numpy as np
    from PIL import Image

    image = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
    result, _ = _get_engine()(image, return_word_box=True)
    boxes: list[dict] = []
    for item in result or []:
        points, text, score = item[0], str(item[1]).strip(), float(item[2])
        if not text or score < MIN_SCORE:
            continue
        # with return_word_box=True: item = [points, text, score, char_boxes, chars, char_scores]
        char_boxes = item[3] if len(item) > 4 else None
        chars = item[4] if len(item) > 4 else None
        if char_boxes and chars and len(char_boxes) == len(chars):
            boxes.extend(_split_on_gaps(char_boxes, chars, score))
        else:
            boxes.append({"text": text, "bbox": _points_to_bbox(points), "score": score})
    return boxes


def _points_to_bbox(points: list) -> list[int]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
    return [x0, y0, max(1, x1 - x0), max(1, y1 - y0)]


def _split_on_gaps(char_boxes: list, chars: list[str], score: float) -> list[dict]:
    """One OCR box can span several menu items ("Edit  View  Search"); split it on wide gaps."""
    segments: list[tuple[str, list[list[int]]]] = []
    text, rects, last_right = "", [], None
    for points, ch in zip(char_boxes, chars):
        if not ch.strip():
            text += " " if text and not text.endswith(" ") else ""
            continue
        bbox = _points_to_bbox(points)
        if last_right is not None and bbox[0] - last_right > SPLIT_GAP * bbox[3]:
            segments.append((text.strip(), rects))
            text, rects = "", []
        text += ch
        rects.append(bbox)
        last_right = bbox[0] + bbox[2]
    if rects:
        segments.append((text.strip(), rects))

    out = []
    for seg_text, seg_rects in segments:
        # character boxes are tight; pad a little so the box covers the whole word
        x0 = max(0, min(r[0] for r in seg_rects) - CHAR_PAD)
        y0 = max(0, min(r[1] for r in seg_rects) - CHAR_PAD)
        x1 = max(r[0] + r[2] for r in seg_rects) + CHAR_PAD
        y1 = max(r[1] + r[3] for r in seg_rects) + CHAR_PAD
        out.append({"text": seg_text, "bbox": [x0, y0, x1 - x0, y1 - y0], "score": score})
    return out


def _same_line(a: list[int], b: list[int]) -> bool:
    top = max(a[1], b[1])
    bottom = min(a[1] + a[3], b[1] + b[3])
    return bottom - top >= LINE_OVERLAP * min(a[3], b[3])


def merge_lines(boxes: list[dict]) -> list[dict]:
    """Group boxes into lines, merge horizontally close boxes; order top-to-bottom, left-to-right."""
    lines: list[list[dict]] = []
    for box in sorted(boxes, key=lambda b: b["bbox"][1] + b["bbox"][3] / 2):
        for line in lines:
            if _same_line(line[0]["bbox"], box["bbox"]):
                line.append(box)
                break
        else:
            lines.append([box])

    merged: list[dict] = []
    for line in sorted(lines, key=lambda ln: min(b["bbox"][1] for b in ln)):
        line.sort(key=lambda b: b["bbox"][0])
        current: dict | None = None
        for box in line:
            if current is not None:
                cx, cy, cw, ch = current["bbox"]
                bx, by, bw, bh = box["bbox"]
                if bx - (cx + cw) <= WORD_GAP * max(ch, bh):
                    x0, y0 = min(cx, bx), min(cy, by)
                    x1, y1 = max(cx + cw, bx + bw), max(cy + ch, by + bh)
                    current = {"text": f"{current['text']} {box['text']}", "bbox": [x0, y0, x1 - x0, y1 - y0]}
                    continue
                merged.append(current)
            current = {"text": box["text"], "bbox": list(box["bbox"])}
        if current is not None:
            merged.append(current)
    return merged


def to_elements(merged: list[dict]) -> list[dict]:
    return [
        {"ref": i, "role": "text", "name": item["text"][:MAX_NAME_CHARS], "value": None, "bbox": item["bbox"]}
        for i, item in enumerate(merged[:MAX_ELEMENTS], start=1)
    ]


def _perceive_sync(png: bytes, request_id: str, n: int) -> dict:
    parser = os.getenv("DESKTOP_PARSER", "ocr").strip().lower()
    if parser == "omniparser":
        logger.warning("DESKTOP_PARSER=omniparser is not implemented yet; using OCR")

    started = time.perf_counter()
    elements = to_elements(merge_lines(_ocr_boxes(png)))
    ocr_ms = (time.perf_counter() - started) * 1000
    shot = annotate(png, elements, request_id, n)
    logger.info(
        "Desktop snapshot: %d elements, OCR %.0f ms, total %.0f ms",
        len(elements), ocr_ms, (time.perf_counter() - started) * 1000,
    )
    return {
        "source": "desktop",
        "url": None,
        "title": None,
        "elements": elements,
        "text_excerpt": " ".join(e["name"] for e in elements)[:EXCERPT_CHARS],
        "shot": shot,
    }


async def perceive(png: bytes, request_id: str, n: int) -> dict:
    """Return a Snapshot (ORTAK BAĞLAM §7) for the screenshot and save the annotated image."""
    return await asyncio.to_thread(_perceive_sync, png, request_id, n)


def warm_up() -> None:
    """Load the OCR model ahead of the first snapshot (call via asyncio.to_thread at startup)."""
    _get_engine()


def format_for_llm(snapshot: dict) -> str:
    """`[12] text "Dosya"` lines, the same format the browser snapshot uses."""
    return "\n".join(f'[{e["ref"]}] {e["role"]} "{e["name"]}"' for e in snapshot["elements"])
