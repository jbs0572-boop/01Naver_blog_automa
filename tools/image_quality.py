from __future__ import annotations

# pyright: reportAny=false
import hashlib
import ipaddress
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from tools.contract_types import ContractError, JSONMap, JSONValue, SchemaError
from tools.image_contract import (
    AUTOMATED_CHECKS,
    MOBILE_VIEWPORT,
    SCORE_FIELDS,
    has_image_signature,
)
from tools.schema_validation import validate_instance

HASH_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
SIZE_RE: Final = re.compile(r"^[1-9][0-9]*x[1-9][0-9]*$")
ASSET_TYPES: Final = frozenset(
    {
        "official_asset",
        "portrait_grid",
        "character_cards",
        "side_by_side",
        "timeline",
        "process_flow",
        "relationship_map",
        "official_screenshot",
        "map",
        "chart",
        "original_photo",
        "generated_illustration",
        "text_only",
    }
)
SOURCE_POLICIES: Final = frozenset(
    {"official_or_licensed", "verified_source", "generated_allowed", "no_image"}
)
SNAPSHOT: Final = "gpt-image-2.5-flare-2026-09-08"
SCHEMA_PATH: Final = (
    Path(__file__).resolve().parents[1] / "schemas" / "workflow-contract.schema.json"
)


def post_q2_image_review_path(root: Path, run_id: str) -> Path:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ContractError("image review run_id is not a safe path component")
    return root / "metadata" / "quality-reviews" / f"{run_id}-images.jsonl"


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


def _has_digest(record: JSONMap, key: str) -> bool:
    value = record.get(key)
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def _timestamp(value: JSONValue, key: str) -> None:
    if not isinstance(value, str):
        raise ContractError(f"invalid timestamp field: {key}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"invalid timestamp field: {key}") from error
    if parsed.tzinfo is None:
        raise ContractError(f"timestamp must include timezone: {key}")


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


def _check_metadata(record: JSONMap, index: int, metadata_path: Path) -> str:
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
    control = _text(record, "generation_control")
    if control not in {"locked", "unlocked", "unavailable"}:
        raise ContractError(
            f"image metadata record {index} has invalid generation_control"
        )
    if control == "unlocked":
        raise ContractError(
            f"image metadata record {index} is not generation_control=locked"
        )
    if control == "locked" and (
        (
            record.get("production_method", "ai_generation") == "ai_generation"
            and (
                _text(record, "generation_provider") != "openai"
                or _text(record, "generation_model") != "gpt-image-2.5-flare"
                or _text(record, "generation_snapshot") != SNAPSHOT
            )
        )
        or (
            record.get("production_method") == "local_render"
            and (
                _text(record, "generation_provider") != "pillow"
                or _text(record, "generation_model") != "not_applicable"
                or not _text(record, "generation_snapshot").startswith("pillow-")
                or _text(record, "renderer_version") != _text(record, "generation_snapshot")[6:]
                or not _has_digest(record, "renderer_sha256")
                or not _has_digest(record, "input_sha256")
            )
        )
        or record.get("production_method") not in {"ai_generation", "local_render", None}
    ):
        raise ContractError(
            f"image metadata record {index} has unsupported locked provider/model"
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
    if (
        not isinstance(output_path, str)
        or output_path.startswith("/")
        or ".." in Path(output_path).parts
    ):
        raise ContractError(f"image metadata record {index} has unsafe output_path")
    output_file = metadata_path.parent / output_path
    if not output_file.is_file():
        raise ContractError(f"image metadata output is missing: {output_path}")
    if not has_image_signature(output_file):
        raise ContractError(
            f"image metadata output is not a recognized image: {output_path}"
        )
    actual = f"sha256:{hashlib.sha256(output_file.read_bytes()).hexdigest()}"
    if actual != output_digest:
        raise ContractError(f"image metadata output hash changed: {output_path}")
    return control


def validate_image_metadata(path: Path) -> JSONMap:
    records = _records(path)
    controls = [
        _check_metadata(record, index, path)
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
        "production_ready": all(control == "locked" for control in controls),
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
        if (
            mobile_viewport != MOBILE_VIEWPORT
            or not isinstance(mobile_render_path, str)
            or mobile_render_path.startswith("/")
            or ".." in Path(mobile_render_path).parts
        ):
            raise ContractError(
                f"image quality record {index} is missing the fixed mobile render evidence"
            )
        if not (path.parent / mobile_render_path).is_file():
            raise ContractError(
                f"image quality render evidence is missing: {mobile_render_path}"
            )
        if not has_image_signature(path.parent / mobile_render_path):
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
        actual_mobile_digest = f"sha256:{hashlib.sha256((path.parent / mobile_render_path).read_bytes()).hexdigest()}"
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


def validate_image_map(
    image_map: Path, body_paths: list[str], thumbnail: str
) -> JSONMap:
    body_entries: list[str] = []
    thumbnail_entries: list[str] = []
    for line in image_map.read_text(encoding="utf-8").splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        values = [cell.strip("`").strip() for cell in cells]
        if len(values) >= 4 and (
            values[2] == "[IMAGE]" or values[2].startswith("[IMAGE:")
        ):
            if len(body_entries) >= len(body_paths):
                raise ContractError("image map has an invalid body image row")
            if values[0] != str(len(body_entries) + 1):
                raise ContractError("image map body image order is not consecutive")
            file_path = values[3]
            if file_path not in body_paths:
                raise ContractError("image map has an unexpected body image path")
            if values[3] != body_paths[len(body_entries)]:
                raise ContractError("image map body image order does not match")
            marker = values[2]
            provenance = _parse_asset_provenance(
                values[12] if len(values) > 12 else ""
            )
            marker_metadata = _parse_image_marker_metadata(marker)
            asset_type = values[5].strip("`").strip() if len(values) > 5 else ""
            source_policy = values[7].strip("`").strip() if len(values) > 7 else ""
            if asset_type.lower() in {"", "x"}:
                asset_type = ""
            if source_policy.lower() in {"", "x"}:
                source_policy = ""
            for field, column_value in (
                ("asset_type", asset_type),
                ("source_policy", source_policy),
            ):
                marker_value = marker_metadata.get(field)
                if marker_value and column_value and marker_value != column_value:
                    raise ContractError(
                        f"image map {field} conflicts with its marker metadata"
                    )
            effective_asset_type = asset_type or marker_metadata.get("asset_type", "")
            effective_source_policy = source_policy or marker_metadata.get(
                "source_policy", ""
            )
            if effective_asset_type and effective_asset_type not in ASSET_TYPES:
                raise ContractError("image map has an unsupported asset_type")
            if effective_source_policy and effective_source_policy not in SOURCE_POLICIES:
                raise ContractError("image map has an unsupported source_policy")
            if effective_source_policy == "official_or_licensed" and (
                provenance.get("origin") not in {"official", "licensed"}
                or not _is_https_source(provenance.get("source_url"))
            ):
                raise ContractError(
                    "official_or_licensed image lacks official or licensed source provenance"
                )
            if effective_asset_type == "map" and (
                provenance.get("origin") != "official"
                or not _is_https_source(provenance.get("source_url"))
            ):
                raise ContractError(
                    "map assets require an official map source URL; generated route diagrams are not maps"
                )
            body_entries.append(file_path)
        elif (
            len(values) >= 3
            and values[0] == "[THUMBNAIL]"
            and values[1] == "[THUMBNAIL]"
        ):
            file_path = values[2]
            if file_path != thumbnail:
                raise ContractError("image map has an invalid thumbnail row")
            thumbnail_entries.append(file_path)
    if body_entries != body_paths or thumbnail_entries != [thumbnail]:
        raise ContractError("image map does not exactly cover ordered assets")
    return {"body_images": len(body_entries), "thumbnail": thumbnail, "passed": True}


def _parse_asset_provenance(value: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in value.strip().strip("`").split(";"):
        key, separator, field_value = part.strip().partition("=")
        if separator and key and field_value:
            fields[key] = field_value
    return fields


def _parse_image_marker_metadata(marker: str) -> dict[str, str]:
    content = marker.strip().strip("`").strip()
    if content.startswith("[IMAGE:") and content.endswith("]"):
        return _parse_asset_provenance(content[len("[IMAGE:") : -1])
    return {}


def _is_https_source(value: str | None) -> bool:
    if (
        value is None
        or not value
        or any(char.isspace() or ord(char) < 32 for char in value)
        or re.search(r"%(?![A-Fa-f0-9]{2})", value)
        or re.search(r'[<>"{}|\\^`]', value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    try:
        _ = ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    try:
        ascii_host = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_host) > 253 or "." not in ascii_host:
        return False
    labels = ascii_host.rstrip(".").split(".")
    return all(
        label
        and len(label) <= 63
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    )


def validate_image_stage_assets(asset_dir: Path, draft_path: Path) -> JSONMap:
    image_map = asset_dir / "image-map.md"
    metadata_path = asset_dir / "image-generation.jsonl"
    quality_path = asset_dir / "image-quality.jsonl"
    required = (image_map, metadata_path, quality_path)
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise ContractError(
            "image-maker is missing required records: " + ", ".join(missing)
        )
    if not draft_path.is_file():
        raise ContractError("image-maker draft input is missing")
    thumbnail_names = ("thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg")
    thumbnails = [asset_dir / name for name in thumbnail_names if (asset_dir / name).is_file()]
    if len(thumbnails) != 1:
        raise ContractError("image-maker must produce exactly one canonical thumbnail")

    _ = validate_image_metadata(metadata_path)
    _ = validate_image_quality(quality_path)
    metadata = _records(metadata_path)
    quality = _records(quality_path)
    output_paths = [record.get("output_path") for record in metadata]
    output_digests = [record.get("output_sha256") for record in metadata]
    if any(not isinstance(value, str) for value in output_paths):
        raise ContractError("image metadata contains an invalid output path")
    if any(not isinstance(value, str) for value in output_digests):
        raise ContractError("image metadata contains an invalid output hash")
    paths = [str(value) for value in output_paths]
    digests = [str(value) for value in output_digests]
    if len(set(paths)) != len(paths) or len(set(digests)) != len(digests):
        raise ContractError("image metadata contains duplicate asset outputs")
    if any(Path(value).name != value for value in paths):
        raise ContractError("image metadata outputs must be direct topic assets")
    thumbnail = thumbnails[0].name
    if paths.count(thumbnail) != 1 or len(paths) - 1 != draft_path.read_text(
        encoding="utf-8"
    ).count("[IMAGE:"):
        raise ContractError("image outputs do not match draft markers and thumbnail")

    quality_digests = [record.get("image_sha256") for record in quality]
    if any(not isinstance(value, str) for value in quality_digests):
        raise ContractError("image quality record contains an invalid image hash")
    quality_values = [str(value) for value in quality_digests]
    if len(quality_values) != len(digests) or set(quality_values) != set(digests):
        raise ContractError("image quality records do not cover every generated asset")
    mobile_paths = [str(record["mobile_render_path"]) for record in quality]
    if set(paths) & set(mobile_paths):
        raise ContractError("mobile render evidence cannot replace a generated asset")
    expected_files = set(paths) | set(mobile_paths) | {
        image_map.name,
        metadata_path.name,
        quality_path.name,
    }
    actual_files = {
        path.relative_to(asset_dir).as_posix()
        for path in asset_dir.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise ContractError("image-maker produced undeclared or incomplete assets")
    _ = validate_image_map(
        image_map,
        [path for path in paths if path != thumbnail],
        thumbnail,
    )
    return {
        "body_markers": len(paths) - 1,
        "outputs": len(paths),
        "thumbnail": thumbnail,
        "image_quality_records": len(quality),
        "passed": True,
    }
