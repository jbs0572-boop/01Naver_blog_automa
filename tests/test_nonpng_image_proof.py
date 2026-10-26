from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from tools.image_quality import validate_image_metadata, validate_image_quality
from tools.workflow_contract import ContractError


def _image(fmt: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (17, 10), (43, 120, 207)).save(output, format=fmt)
    return output.getvalue()


def _metadata(path: Path) -> dict[str, object]:
    return {
        "generation_provider": "openai",
        "generation_model": "gpt-image-2",
        "generation_snapshot": "gpt-image-2-2026-04-21",
        "generation_control": "locked",
        "quality": "high",
        "size": "17x10",
        "prompt_template_version": "image-prompt-v1",
        "prompt_sha256": "sha256:" + "1" * 64,
        "reference_sha256": [],
        "output_sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "output_path": path.name,
        "generated_at": "2026-10-26T12:00:00+09:00",
        "provenance_status": "generated",
    }


def _quality(path: Path) -> dict[str, object]:
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
        "mobile_render_path": path.name,
        "mobile_render_sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "human_verdict": "passed",
    }


def _proofs(tmp_path: Path, raw: bytes) -> tuple[Path, Path]:
    image = tmp_path / "image.bin"
    image.write_bytes(raw)
    metadata = tmp_path / "image-generation.jsonl"
    quality = tmp_path / "image-quality.jsonl"
    metadata.write_text(json.dumps(_metadata(image)) + "\n", encoding="utf-8")
    quality.write_text(json.dumps(_quality(image)) + "\n", encoding="utf-8")
    return metadata, quality


@pytest.mark.parametrize("fmt", ["JPEG", "GIF", "WEBP"])
def test_valid_non_png_proofs_decode_in_metadata_and_mobile_quality(
    tmp_path: Path, fmt: str,
) -> None:
    metadata, quality = _proofs(tmp_path, _image(fmt))
    assert validate_image_metadata(metadata, "formal")["formal_ready"] is True
    assert validate_image_quality(quality)["passed"] is True


@pytest.mark.parametrize(
    "forged",
    [
        b"\xff\xd8\xff" + b"\x00" * 24 + b"\xff\xd9",
        b"GIF89a" + b"\x00" * 24 + b"\x3b",
        b"RIFF" + (16).to_bytes(4, "little") + b"WEBP"
        + b"VP8 " + b"\x00" * 4,
    ],
)
def test_recognized_magic_without_readable_pixels_is_rejected(
    tmp_path: Path, forged: bytes,
) -> None:
    metadata, quality = _proofs(tmp_path, forged)
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_metadata(metadata, "formal")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_quality(quality)


@pytest.mark.parametrize("fmt", ["JPEG", "GIF", "WEBP"])
def test_truncated_non_png_with_matching_hash_still_fails(
    tmp_path: Path, fmt: str,
) -> None:
    original = _image(fmt)
    # Preserve the end marker where relevant: a magic/end check is not a decoder.
    broken = original[: len(original) // 2] + (
        b"\xff\xd9" if fmt == "JPEG" else b"\x3b" if fmt == "GIF" else b""
    )
    metadata, quality = _proofs(tmp_path, broken)
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_metadata(metadata, "formal")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_quality(quality)


def test_animated_gif_rejects_corrupted_later_frame(tmp_path: Path) -> None:
    output = BytesIO()
    frames = [Image.new("RGB", (17, 10), name) for name in ("red", "blue", "green")]
    frames[0].save(
        output, format="GIF", save_all=True,
        append_images=frames[1:], loop=0, duration=100,
    )
    raw = output.getvalue()
    assert validate_image_metadata(
        _proofs(tmp_path, raw)[0], "formal",
    )["formal_ready"] is True
    # First frame intact, later frame truncated; some decoders raise IndexError
    # while counting GIF frames instead of during pixel decoding.
    metadata, quality = _proofs(tmp_path, raw[: int(len(raw) * 0.8)] + b"\x3b")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_metadata(metadata, "formal")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_quality(quality)


def test_image_files_are_decoded_and_hashed_from_one_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata, quality = _proofs(tmp_path, _image("JPEG"))
    image = tmp_path / "image.bin"
    original_open = Path.open
    reads = 0

    def counted_open(path: Path, *args: object, **kwargs: object):
        nonlocal reads
        if path == image:
            reads += 1
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    assert validate_image_metadata(metadata, "formal")["formal_ready"] is True
    assert validate_image_quality(quality)["passed"] is True
    assert reads == 2  # Once for each public validator, not twice per proof.


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
def test_oversized_image_proofs_fail_before_unbounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fmt: str,
) -> None:
    import tools.image_quality as image_quality

    raw = _image(fmt)
    metadata, quality = _proofs(tmp_path, raw)
    image = tmp_path / "image.bin"
    # Exercise the real size-limit path without allocating a multi-GB fixture.
    # Production retains its explicit 256 MiB upper bound.
    assert image_quality.MAX_IMAGE_EVIDENCE_BYTES == 256 * 1024 * 1024
    monkeypatch.setattr(image_quality, "MAX_IMAGE_EVIDENCE_BYTES", len(raw) - 1)

    original_read = Path.read_bytes

    def forbid_unbounded_read(path: Path) -> bytes:
        if path == image:
            raise AssertionError("image proof must not use unbounded read_bytes")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", forbid_unbounded_read)
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_metadata(metadata, "formal")
    with pytest.raises(ContractError, match="not a recognized image"):
        validate_image_quality(quality)
