from __future__ import annotations

# pyright: reportAny=false
import hashlib
import json
import re
import struct
import zlib
from datetime import datetime
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
        if kind == b"IDAT":
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


def _has_image_signature(path: Path) -> bool:
    raw = path.read_bytes()
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return _complete_png(raw)
    # Other formats retain their existing signature-level check; they
    # require a separate decoder-level follow-up.
    return raw.startswith((b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")) and (
        b"WEBP" in raw[:16] or raw.startswith((b"\xff\xd8\xff", b"GIF87a", b"GIF89a"))
    )


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
    if not _has_image_signature(output_file):
        raise ContractError(
            f"image metadata output is not a recognized image: {output_path}"
        )
    actual = f"sha256:{hashlib.sha256(output_file.read_bytes()).hexdigest()}"
    if actual != output_digest:
        raise ContractError(f"image metadata output hash changed: {output_path}")
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
        if not _has_image_signature(mobile_file):
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
        actual_mobile_digest = f"sha256:{hashlib.sha256(mobile_file.read_bytes()).hexdigest()}"
        if actual_mobile_digest != mobile_render_digest:
            raise ContractError(
                f"image quality render evidence changed: {mobile_render_path}"
            )
        if (
            record.get("immediate_failure") is not False
            or record.get("mobile_rendered") is not True
            or record.get("human_verdict") != "passed"
        ):
            raise ContractError(f"image quality record {index} has not passed Q3")
    return {"quality": str(path), "records": len(records), "passed": True}
