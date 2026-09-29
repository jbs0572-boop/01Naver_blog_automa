from __future__ import annotations

from pathlib import Path
from typing import Final

from PIL import Image

SCORE_FIELDS: Final = (
    "subject_relevance",
    "composition_legibility",
    "rendering_completion",
    "information_contribution",
    "style_consistency",
)
AUTOMATED_CHECKS: Final = (
    "decode_check",
    "duplicate_check",
    "ocr_check",
    "visual_contract_check",
    "mobile_render_check",
)
MOBILE_VIEWPORT: Final = "390x844"


def has_image_signature(path: Path) -> bool:
    supported_formats = {"GIF", "JPEG", "PNG", "WEBP"}
    try:
        with Image.open(path) as image:
            if image.format not in supported_formats:
                return False
            width, height = image.size
            if width < 1 or height < 1 or width * height > 50_000_000:
                return False
            image.verify()
        with Image.open(path) as image:
            if image.format not in supported_formats:
                return False
            _ = image.load()
            width, height = image.size
            return width >= 1 and height >= 1 and width * height <= 50_000_000
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        return False


__all__ = ["AUTOMATED_CHECKS", "MOBILE_VIEWPORT", "SCORE_FIELDS", "has_image_signature"]
