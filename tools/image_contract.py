from __future__ import annotations

import struct
from pathlib import Path
from typing import Final

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
    raw = path.read_bytes()
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return (
            len(raw) >= 24
            and raw[12:16] == b"IHDR"
            and struct.unpack(">II", raw[16:24]) > (0, 0)
        )
    signatures = (b"\xff\xd8\xff", b"GIF87a", b"GIF89a")
    return raw.startswith((*signatures, b"RIFF")) and (
        b"WEBP" in raw[:16] or raw.startswith(signatures)
    )


__all__ = ["AUTOMATED_CHECKS", "MOBILE_VIEWPORT", "SCORE_FIELDS", "has_image_signature"]
