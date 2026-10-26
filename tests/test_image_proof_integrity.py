from __future__ import annotations

import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from tools.image_quality import validate_image_metadata, validate_image_quality
from tools.workflow_contract import ContractError

VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _corrupt_png(case: str) -> bytes:
    if case == "truncated":
        return VALID_PNG[:24]  # Previously accepted: correct magic and IHDR.
    if case == "missing_iend":
        return VALID_PNG[:-12]
    if case == "trailing_bytes":
        return VALID_PNG + b"not-image-data"
    if case == "bad_crc":
        changed = bytearray(VALID_PNG)
        changed[45] ^= 0x01
        return bytes(changed)
    if case == "bad_deflate":
        changed = bytearray(VALID_PNG)
        chunk_start = changed.index(b"IDAT")
        length = int.from_bytes(changed[chunk_start - 4 : chunk_start], "big")
        payload_start = chunk_start + 4
        changed[payload_start] ^= 0xFF
        chunk_end = payload_start + length
        crc = zlib.crc32(changed[chunk_start:chunk_end])
        changed[chunk_end:chunk_end + 4] = crc.to_bytes(4, "big")
        return bytes(changed)
    raise AssertionError(case)


def _metadata(image: Path) -> dict[str, object]:
    return {
        "generation_provider": "openai",
        "generation_model": "gpt-image-2",
        "generation_snapshot": "gpt-image-2-2026-04-21",
        "generation_control": "locked",
        "quality": "high",
        "size": "1600x900",
        "prompt_template_version": "image-prompt-v1",
        "prompt_sha256": "sha256:" + "1" * 64,
        "reference_sha256": [],
        "output_sha256": "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest(),
        "output_path": image.name,
        "generated_at": "2026-08-27T10:00:00+09:00",
        "provenance_status": "generated",
    }


def _quality(image: Path) -> dict[str, object]:
    return {
        "automated_checks": {
            key: "passed"
            for key in (
                "decode_check", "duplicate_check", "ocr_check",
                "visual_contract_check", "mobile_render_check",
            )
        },
        "scores": {
            "subject_relevance": 4,
            "composition_legibility": 4,
            "rendering_completion": 3,
            "information_contribution": 4,
            "style_consistency": 3,
        },
        "immediate_failure": False,
        "mobile_rendered": True,
        "mobile_viewport": "390x844",
        "mobile_render_path": image.name,
        "mobile_render_sha256": "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest(),
        "human_verdict": "passed",
    }


@pytest.mark.parametrize(
    "case",
    ["truncated", "missing_iend", "trailing_bytes", "bad_crc", "bad_deflate"],
)
def test_corrupt_png_never_passes_formal_image_proof(tmp_path: Path, case: str) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(_corrupt_png(case))
    metadata = tmp_path / "image-generation.jsonl"
    metadata.write_text(json.dumps(_metadata(image)) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_metadata(metadata, "formal")


def test_corrupt_mobile_render_does_not_pass_claimed_checks(tmp_path: Path) -> None:
    image = tmp_path / "mobile.png"
    image.write_bytes(VALID_PNG[:24])
    quality = tmp_path / "image-quality.jsonl"
    quality.write_text(json.dumps(_quality(image)) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_quality(quality)


def test_valid_png_remains_accepted_as_metadata_and_mobile_proof(tmp_path: Path) -> None:
    image = tmp_path / "real.png"
    image.write_bytes(VALID_PNG)
    metadata = tmp_path / "image-generation.jsonl"
    quality = tmp_path / "image-quality.jsonl"
    metadata.write_text(json.dumps(_metadata(image)) + "\n", encoding="utf-8")
    quality.write_text(json.dumps(_quality(image)) + "\n", encoding="utf-8")
    assert validate_image_metadata(metadata, "formal")["formal_ready"] is True
    assert validate_image_quality(quality)["passed"] is True


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        len(data).to_bytes(4, "big") + kind + data
        + zlib.crc32(kind + data).to_bytes(4, "big")
    )


def _indexed_png(palette: bytes | None, *, after_idat: bool = False) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0)
    chunks = [
        _chunk(b"IHDR", ihdr),
        _chunk(b"IDAT", zlib.compress(b"\x00\x00")),
    ]
    if palette is not None:
        if after_idat:
            chunks.append(_chunk(b"PLTE", palette))
        else:
            chunks.insert(1, _chunk(b"PLTE", palette))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks) + _chunk(b"IEND", b"")


@pytest.mark.parametrize(
    ("palette", "after_idat"),
    [
        (None, False),
        (b"invalid-length", False),
        (b"\x00\x00\x00", True),
        (bytes(257 * 3), False),
    ],
)
def test_indexed_png_requires_valid_palette_before_idat(
    tmp_path: Path, palette: bytes | None, after_idat: bool
) -> None:
    image = tmp_path / "indexed.png"
    image.write_bytes(_indexed_png(palette, after_idat=after_idat))
    metadata = tmp_path / "indexed.jsonl"
    metadata.write_text(json.dumps(_metadata(image)) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_metadata(metadata, "formal")


def test_valid_indexed_png_with_palette_is_accepted(tmp_path: Path) -> None:
    image = tmp_path / "indexed.png"
    image.write_bytes(_indexed_png(b"\x00\x00\x00"))
    metadata = tmp_path / "indexed.jsonl"
    metadata.write_text(json.dumps(_metadata(image)) + "\n", encoding="utf-8")
    assert validate_image_metadata(metadata, "formal")["formal_ready"] is True


def _rechunk_valid_png(*, separate_idat: bool) -> bytes:
    """Split a real fixture's zlib stream; only interleaving is invalid."""
    marker = VALID_PNG.index(b"IDAT")
    start = marker - 4
    length = int.from_bytes(VALID_PNG[start:marker], "big")
    compressed = VALID_PNG[marker + 4:marker + 4 + length]
    midpoint = max(1, len(compressed) // 2)
    between = _chunk(b"tEXt", b"Comment\x00proof") if separate_idat else b""
    return (
        VALID_PNG[:start]
        + _chunk(b"IDAT", compressed[:midpoint])
        + between
        + _chunk(b"IDAT", compressed[midpoint:])
        + VALID_PNG[marker + 8 + length:]
    )


@pytest.mark.parametrize("kind", [b"FAKE", b"abcD"])
def test_png_rejects_unknown_critical_or_invalid_reserved_chunk(
    tmp_path: Path, kind: bytes
) -> None:
    image = tmp_path / "bad-chunk.png"
    # A correct CRC does not make an unknown critical chunk decodable.
    image.write_bytes(VALID_PNG[:-12] + _chunk(kind, b"") + VALID_PNG[-12:])
    metadata = tmp_path / "metadata.jsonl"
    metadata.write_text(json.dumps(_metadata(image)) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_metadata(metadata, "formal")


def test_png_rejects_ancillary_chunk_between_idat_chunks(tmp_path: Path) -> None:
    image = tmp_path / "out-of-order.png"
    image.write_bytes(_rechunk_valid_png(separate_idat=True))
    metadata = tmp_path / "metadata.jsonl"
    metadata.write_text(json.dumps(_metadata(image)) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_metadata(metadata, "formal")


def test_png_accepts_consecutive_idat_and_trailing_ancillary(tmp_path: Path) -> None:
    for name, data in (
        ("consecutive.png", _rechunk_valid_png(separate_idat=False)),
        ("trailing-text.png", VALID_PNG[:-12] + _chunk(b"tEXt", b"Note\x00ok") + VALID_PNG[-12:]),
    ):
        image = tmp_path / name
        image.write_bytes(data)
        metadata = tmp_path / f"{name}.jsonl"
        metadata.write_text(json.dumps(_metadata(image)) + "\n", encoding="utf-8")
        assert validate_image_metadata(metadata, "formal")["formal_ready"] is True
