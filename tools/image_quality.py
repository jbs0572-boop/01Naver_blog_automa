from __future__ import annotations

# pyright: reportAny=false
import hashlib
import json
import re
import struct
import warnings
import zlib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Final

from tools.workflow_contract import (
    ContractError,
    JSONMap,
    JSONValue,
    SchemaError,
    validate_instance,
)

HASH_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
SIZE_RE: Final = re.compile(r"^[1-9][0-9]*x[1-9][0-9]*$")
SNAPSHOT: Final = "gpt-image-2-2026-04-21"
SCHEMA_PATH: Final = (
    Path(__file__).resolve().parents[1] / "schemas" / "workflow-contract.schema.json"
)
METADATA_FIELDS: Final = (
    "generation_provider",
    "generation_model",
    "generation_snapshot",
    "generation_control",
    "quality",
    "size",
    "prompt_template_version",
    "prompt_sha256",
    "reference_sha256",
    "output_sha256",
    "generated_at",
    "provenance_status",
    "output_path",
)
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
# Bound raw image evidence before decoding or hashing; sparse/hostile files
# must never cause an unbounded Path.read_bytes() allocation.
MAX_IMAGE_EVIDENCE_BYTES: Final = 256 * 1024 * 1024


def _map(value: JSONValue, label: str) -> JSONMap:
    if not isinstance(value, dict):
        raise ContractError(f"JSON object required: {label}")
    return value


def _text(data: JSONMap, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(f"missing or invalid image field: {key}")
    return value


def _digest(value: JSONValue, key: str) -> str:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise ContractError(f"invalid SHA-256 field: {key}")
    return value


def _timestamp(value: JSONValue, key: str) -> None:
    if not isinstance(value, str):
        raise ContractError(f"invalid timestamp field: {key}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"invalid timestamp field: {key}") from error
    if parsed.tzinfo is None:
        raise ContractError(f"timestamp must include timezone: {key}")


def _complete_png(raw: bytes) -> bool:
    """Verify PNG chunk CRCs, compressed scanlines and the final IEND chunk.

    A valid PNG signature and IHDR alone do not prove the image is readable.
    Check the bounded raster stream without requiring an image library in
    the legacy workflow validator. This does not replace visual inspection.
    """
    offset = 8
    header: tuple[int, int, int, int, int] | None = None
    image_data: list[bytes] = []
    palette_entries = 0
    idat_ended = False
    while offset + 12 <= len(raw):
        length = struct.unpack(">I", raw[offset : offset + 4])[0]
        end = offset + 12 + length
        if end > len(raw):
            return False
        kind = raw[offset + 4 : offset + 8]
        payload = raw[offset + 8 : offset + 8 + length]
        recorded_crc = struct.unpack(">I", raw[end - 4 : end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != recorded_crc:
            return False
        # PNG chunk names must be ASCII letters with the reserved bit clear.
        # Unknown critical chunks require a decoder we do not implement, so
        # claiming the image is readable would be unsafe.
        if (
            any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in kind)
            or not (65 <= kind[2] <= 90)
            or (
                65 <= kind[0] <= 90
                and kind not in {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
            )
        ):
            return False
        if header is None:
            if kind != b"IHDR" or length != 13:
                return False
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
            depths = {
                0: (1, 2, 4, 8, 16),
                2: (8, 16),
                3: (1, 2, 4, 8),
                4: (8, 16),
                6: (8, 16),
            }
            if (
                not width or not height or channels is None
                or depth not in depths[color]
                or compression != 0 or filtering != 0 or interlace not in (0, 1)
            ):
                return False
            header = width, height, depth, channels, interlace
        elif kind == b"IHDR":
            return False
        # All IDAT chunks must be consecutive. Otherwise identical compressed
        # bytes can pass our zlib check while violating the PNG file format.
        if kind != b"IDAT" and image_data:
            idat_ended = True
        if kind == b"IDAT" and idat_ended:
            return False
        if kind == b"PLTE":
            # Indexed pixels have no meaning without a real palette. A
            # palette must precede IDAT and contain 1..2**depth RGB entries.
            if (
                palette_entries or image_data or color in {0, 4}
                or not length or length % 3 or length > 768
                or (color == 3 and length // 3 > (1 << depth))
            ):
                return False
            palette_entries = length // 3
        if kind == b"IDAT":
            if color == 3 and not palette_entries:
                return False
            image_data.append(payload)
        if kind == b"IEND":
            if length or end != len(raw) or not image_data:
                return False
            width, height, depth, channels, interlace = header
            passes = (
                ((0, 0, 1, 1),)
                if interlace == 0
                else (
                    (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8),
                    (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
                    (0, 1, 1, 2),
                )
            )
            rows: list[tuple[int, int]] = []
            expected = 0
            for x, y, dx, dy in passes:
                pass_width = max(0, (width - x + dx - 1) // dx)
                pass_height = max(0, (height - y + dy - 1) // dy)
                if pass_width and pass_height:
                    stride = 1 + (pass_width * depth * channels + 7) // 8
                    expected += pass_height * stride
                    rows.append((pass_height, stride))
            # Reject decompression bombs, including forged large IHDR values.
            if expected > 256 * 1024 * 1024:
                return False
            try:
                decoder = zlib.decompressobj()
                pixels = decoder.decompress(b"".join(image_data), expected + 1)
            except zlib.error:
                return False
            if (
                len(pixels) != expected or not decoder.eof
                or decoder.unused_data or decoder.unconsumed_tail
            ):
                return False
            position = 0
            for height_in_pass, stride in rows:
                for _ in range(height_in_pass):
                    if pixels[position] > 4:  # PNG scanline filter byte
                        return False
                    position += stride
            return True
        offset = end
    return False


def _complete_non_png(raw: bytes) -> bool:
    """Decode every frame, not just the magic bytes, of JPEG/GIF/WebP evidence.

    Pillow is already required by the editorial image pipeline and installed
    in the Python-quality CI. Without a decoder we fail closed; a file header
    cannot establish that an image is readable.
    """
    if raw.startswith(b"\xff\xd8\xff"):
        expected_format = "JPEG"
        if not raw.endswith(b"\xff\xd9"):
            return False
    elif raw.startswith((b"GIF87a", b"GIF89a")):
        expected_format = "GIF"
        if not raw.endswith(b"\x3b"):
            return False
    elif len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        expected_format = "WEBP"
        if int.from_bytes(raw[4:8], "little") != len(raw) - 8:
            return False
    else:
        return False

    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # verify() checks file structure; load() checks compressed pixels.
            # They require independent decoders because verify() closes the
            # first decoder. Both use the very same immutable bytes.
            with Image.open(BytesIO(raw)) as image:
                if image.format != expected_format:
                    return False
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                if image.format != expected_format:
                    return False
                frames = getattr(image, "n_frames", 1)
                if frames < 1 or frames > 256:
                    return False
                # Bound animated GIF/WebP and decompression resource use.
                frame_pixels = image.width * image.height
                if frame_pixels <= 0 or frame_pixels * frames > 64_000_000:
                    return False
                for index in range(frames):
                    image.seek(index)
                    image.load()
        return True
    except (
        OSError, ValueError, SyntaxError, EOFError, IndexError, struct.error,
        Image.DecompressionBombError, Image.DecompressionBombWarning,
    ):
        return False


def _verified_image_bytes(path: Path) -> bytes | None:
    """Read at most one bounded snapshot for both decoding and SHA-256.

    A giant or concurrently growing evidence file is rejected before a
    full-file allocation. Never hash different bytes from those decoded.
    """
    with path.open("rb") as stream:
        raw = stream.read(MAX_IMAGE_EVIDENCE_BYTES + 1)
    if len(raw) > MAX_IMAGE_EVIDENCE_BYTES:
        return None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return raw if _complete_png(raw) else None
    return raw if _complete_non_png(raw) else None


def _decoded_dimensions(raw: bytes) -> tuple[int, int]:
    """Read dimensions from the same fully validated and hashed image bytes.

    PNG dimensions come from its already CRC-verified IHDR. The other formats
    have passed full Pillow verification before their header is inspected.
    """
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", raw[16:24])
    try:
        from PIL import Image
        with Image.open(BytesIO(raw)) as image:
            return image.size
    except (ImportError, OSError, ValueError, SyntaxError) as error:
        raise ContractError("verified image dimensions unavailable") from error


def _records(path: Path) -> list[JSONMap]:
    records: list[JSONMap] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw: JSONValue = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(f"invalid JSON at {path}:{line_no}") from error
        records.append(_map(raw, f"{path}:{line_no}"))
    if not records:
        raise ContractError(f"image metadata is empty: {path}")
    return records


def _local_evidence_path(record_path: Path, value: JSONValue, field: str) -> Path:
    """Resolve image evidence within its JSONL folder, including symlink targets."""
    if (
        not isinstance(value, str)
        or not value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise ContractError(f"unsafe {field}")
    base = record_path.parent.resolve()
    try:
        resolved = (base / value).resolve()
    except (OSError, RuntimeError) as error:
        raise ContractError(f"unsafe {field}") from error
    if not resolved.is_relative_to(base):
        raise ContractError(f"unsafe {field}: symlink escapes evidence directory")
    return resolved


def _check_metadata(record: JSONMap, index: int, mode: str, metadata_path: Path) -> str:
    try:
        validate_instance(record, SCHEMA_PATH)
    except SchemaError as error:
        raise ContractError(
            f"image metadata record {index} does not match workflow schema: {error}"
        ) from error
    missing = [key for key in METADATA_FIELDS if key not in record]
    if missing:
        raise ContractError(
            f"image metadata record {index} is missing: {', '.join(missing)}"
        )
    if (
        _text(record, "generation_provider") != "openai"
        or _text(record, "generation_model") != "gpt-image-2"
    ):
        raise ContractError(
            f"image metadata record {index} has unsupported provider or model"
        )
    if _text(record, "generation_snapshot") != SNAPSHOT:
        raise ContractError(
            f"image metadata record {index} has unsupported model snapshot"
        )
    control = _text(record, "generation_control")
    if control not in {"locked", "unlocked"}:
        raise ContractError(
            f"image metadata record {index} has invalid generation_control"
        )
    if mode == "formal" and control != "locked":
        raise ContractError(
            f"formal image metadata record {index} is not generation_control=locked"
        )
    if _text(record, "quality") != "high" or not SIZE_RE.fullmatch(
        _text(record, "size")
    ):
        raise ContractError(
            f"image metadata record {index} has invalid quality or size"
        )
    _ = _text(record, "prompt_template_version")
    _ = _digest(record["prompt_sha256"], "prompt_sha256")
    references = record["reference_sha256"]
    if not isinstance(references, list) or any(
        not isinstance(value, str) or not HASH_RE.fullmatch(value)
        for value in references
    ):
        raise ContractError(
            f"image metadata record {index} has invalid reference_sha256"
        )
    output_digest = _digest(record["output_sha256"], "output_sha256")
    _timestamp(record["generated_at"], "generated_at")
    if _text(record, "provenance_status") not in {
        "generated",
        "official",
        "licensed",
        "captured",
    }:
        raise ContractError(
            f"image metadata record {index} has invalid provenance_status"
        )
    if "seed" in record:
        raise ContractError(f"image metadata record {index} contains unsupported seed")
    output_path = record["output_path"]
    output_file = _local_evidence_path(metadata_path, output_path, "output_path")
    if not output_file.is_file():
        raise ContractError(f"image metadata output is missing: {output_path}")
    verified_bytes = _verified_image_bytes(output_file)
    if verified_bytes is None:
        raise ContractError(
            f"image metadata output is not a recognized image: {output_path}"
        )
    actual = f"sha256:{hashlib.sha256(verified_bytes).hexdigest()}"
    if actual != output_digest:
        raise ContractError(f"image metadata output hash changed: {output_path}")
    actual_width, actual_height = _decoded_dimensions(verified_bytes)
    if _text(record, "size") != f"{actual_width}x{actual_height}":
        raise ContractError(
            f"image metadata output size does not match decoded pixels: {output_path}"
        )
    return control


def validate_image_metadata(path: Path, mode: str) -> JSONMap:
    if mode not in {"beta", "formal"}:
        raise ContractError("mode must be beta or formal")
    records = _records(path)
    controls = [
        _check_metadata(record, index, mode, path)
        for index, record in enumerate(records, 1)
    ]
    output_digests = [record.get("output_sha256") for record in records]
    if len({digest for digest in output_digests if isinstance(digest, str)}) != len(
        output_digests
    ):
        raise ContractError("image metadata contains duplicate output_sha256 values")
    return {
        "metadata": str(path),
        "records": len(records),
        "generation_control": {
            "locked": controls.count("locked"),
            "unlocked": controls.count("unlocked"),
        },
        "formal_ready": all(control == "locked" for control in controls),
    }


def validate_image_quality(path: Path) -> JSONMap:
    records = _records(path)
    for index, record in enumerate(records, 1):
        try:
            validate_instance(record, SCHEMA_PATH)
        except SchemaError as error:
            raise ContractError(
                f"image quality record {index} does not match workflow schema: {error}"
            ) from error
        checks = record.get("automated_checks")
        if not isinstance(checks, dict) or any(
            checks.get(key) != "passed" for key in AUTOMATED_CHECKS
        ):
            raise ContractError(
                f"image quality record {index} has a failed automated check"
            )
        scores = record.get("scores")
        if not isinstance(scores, dict):
            raise ContractError(f"image quality record {index} is missing scores")
        values: list[int] = []
        for key in SCORE_FIELDS:
            value = scores.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or value > 4
            ):
                raise ContractError(
                    f"image quality record {index} has invalid score: {key}"
                )
            values.append(value)
        if sum(values) < 16 or min(values) < 3:
            raise ContractError(
                f"image quality record {index} is below 16/20 or has a score below 3"
            )
        mobile_viewport = record.get("mobile_viewport")
        mobile_render_path = record.get("mobile_render_path")
        if mobile_viewport != MOBILE_VIEWPORT:
            raise ContractError(
                f"image quality record {index} is missing the fixed mobile render evidence"
            )
        mobile_file = _local_evidence_path(path, mobile_render_path, "mobile_render_path")
        if not mobile_file.is_file():
            raise ContractError(
                f"image quality render evidence is missing: {mobile_render_path}"
            )
        verified_bytes = _verified_image_bytes(mobile_file)
        if verified_bytes is None:
            raise ContractError(
                f"image quality render evidence is not a recognized image: {mobile_render_path}"
            )
        mobile_render_digest = record.get("mobile_render_sha256")
        if not isinstance(mobile_render_digest, str) or not HASH_RE.fullmatch(
            mobile_render_digest
        ):
            raise ContractError(
                f"image quality record {index} has invalid mobile render hash"
            )
        actual_mobile_digest = f"sha256:{hashlib.sha256(verified_bytes).hexdigest()}"
        if actual_mobile_digest != mobile_render_digest:
            raise ContractError(
                f"image quality render evidence changed: {mobile_render_path}"
            )
        # A matching checksum and a claimed viewport do not prove that the
        # supplied mobile screenshot actually depicts that viewport. Support
        # common 1x/2x/3x browser device-pixel ratios, not arbitrary thumbnails.
        viewport_width, viewport_height = (
            int(part) for part in MOBILE_VIEWPORT.split("x")
        )
        rendered_width, rendered_height = _decoded_dimensions(verified_bytes)
        if not any(
            rendered_width == viewport_width * scale
            and rendered_height == viewport_height * scale
            for scale in (1, 2, 3)
        ):
            raise ContractError(
                f"image quality record {index} mobile render dimensions do not match viewport"
            )
        if (
            record.get("immediate_failure") is not False
            or record.get("mobile_rendered") is not True
            or record.get("human_verdict") != "passed"
        ):
            raise ContractError(f"image quality record {index} has not passed Q3")
    return {"quality": str(path), "records": len(records), "passed": True}
