from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.contract_types import (
    PIPELINE_VERSION,
    SCHEMA_VERSION,
    ContractError,
    JSONMap,
    JSONValue,
)
from tools.manifest_parsing import (
    as_map,
    aware_datetime,
    ensure_canonical_layout,
    integer_field,
    load_json_map,
    text_field,
)
from tools.schema_validation import SCHEMA_PATH, SchemaError, validate_instance

ROLE_ORDER: Final = {
    "final_markdown": 1,
    "naver_layout": 2,
    "naver_copy": 3,
    "image_map": 4,
    "body_image": 5,
    "thumbnail": 6,
}
IMAGE_RE: Final = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
DIGEST_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ManifestFile:
    role: str
    path: str
    order: int
    size_bytes: int
    sha256: str

    def as_json(self) -> JSONMap:
        return {
            "role": self.role,
            "path": self.path,
            "order": self.order,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class Manifest:
    schema_version: str
    pipeline_version: str
    run_id: str
    topic_id: str
    mode: str
    created_at: str
    files: tuple[ManifestFile, ...]
    artifact_digest: str


def _file_from_json(value: JSONValue) -> ManifestFile:
    data = as_map(value, "manifest.files[]")
    role = text_field(data, "role")
    path = text_field(data, "path")
    order = integer_field(data, "order")
    size_bytes = integer_field(data, "size_bytes")
    sha256 = text_field(data, "sha256")
    if (
        role not in ROLE_ORDER
        or order < 1
        or size_bytes < 0
        or not SHA256_RE.fullmatch(sha256)
    ):
        raise ContractError(f"invalid manifest file record: {path}")
    return ManifestFile(role, path, order, size_bytes, sha256)


def _manifest_from_json(data: JSONMap) -> Manifest:
    values = data.get("files")
    if not isinstance(values, list) or not values:
        raise ContractError("manifest.files must be a non-empty array")
    files = tuple(_file_from_json(value) for value in values)
    manifest = Manifest(
        schema_version=text_field(data, "schema_version"),
        pipeline_version=text_field(data, "pipeline_version"),
        run_id=text_field(data, "run_id"),
        topic_id=text_field(data, "topic_id"),
        mode=text_field(data, "mode"),
        created_at=text_field(data, "created_at"),
        files=files,
        artifact_digest=text_field(data, "artifact_digest"),
    )
    _ = aware_datetime(manifest.created_at, "manifest.created_at")
    if (
        manifest.schema_version != SCHEMA_VERSION
        or manifest.pipeline_version != PIPELINE_VERSION
    ):
        raise ContractError("manifest version is not workflow-optimized-v1")
    if manifest.mode not in {"beta", "formal"} or not DIGEST_RE.fullmatch(
        manifest.artifact_digest
    ):
        raise ContractError("manifest mode or artifact_digest is invalid")
    if tuple(sorted(files, key=lambda item: item.order)) != files:
        raise ContractError("manifest file order is not canonical")
    if len({item.path for item in files}) != len(files) or len(
        {item.order for item in files}
    ) != len(files):
        raise ContractError("manifest file paths and orders must be unique")
    required = {
        "final_markdown",
        "naver_layout",
        "naver_copy",
        "image_map",
        "thumbnail",
    }
    if not required.issubset({item.role for item in files}):
        raise ContractError("manifest is missing a required artifact role")
    ensure_canonical_layout(tuple((item.role, item.path, item.order) for item in files))
    return manifest


def _canonical_digest(data: JSONMap) -> str:
    unsigned = dict(data)
    _ = unsigned.pop("artifact_digest", None)
    encoded = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ContractError(f"artifact is outside workspace: {path}") from error


def _record(root: Path, role: str, order: int, path: Path) -> ManifestFile:
    if not path.is_file():
        raise ContractError(f"required artifact is missing: {path}")
    raw = path.read_bytes()
    return ManifestFile(
        role, _relative(root, path), order, len(raw), hashlib.sha256(raw).hexdigest()
    )


def _thumbnail(asset_dir: Path) -> Path:
    candidates = [
        asset_dir / name
        for name in ("thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg")
        if (asset_dir / name).is_file()
    ]
    if len(candidates) != 1:
        raise ContractError("exactly one thumbnail.png/.jpg/.jpeg is required")
    return candidates[0]


def _body_paths(root: Path, final_path: Path, thumbnail: Path) -> list[Path]:
    if not final_path.is_file():
        raise ContractError(f"required artifact is missing: {final_path}")
    paths: list[Path] = []
    for reference in IMAGE_RE.findall(final_path.read_text(encoding="utf-8")):
        if reference.startswith(("http://", "https://", "data:", "file://", "/")):
            raise ContractError(
                f"external image reference is not canonical: {reference}"
            )
        path = (final_path.parent / reference).resolve()
        if path == thumbnail.resolve():
            raise ContractError("thumbnail must not be embedded as a body image")
        if path in paths:
            raise ContractError(f"duplicate body image reference: {path}")
        if not path.is_file():
            raise ContractError(f"body image is missing: {path}")
        _ = _relative(root, path)
        paths.append(path)
    return paths


def build_manifest(
    root: Path, keyword: str, run_id: str, topic_id: str, mode: str, created_at: str
) -> JSONMap:
    if mode not in {"beta", "formal"}:
        raise ContractError("mode must be beta or formal")
    parts = Path(keyword).parts
    if not keyword or Path(keyword).is_absolute() or ".." in parts or len(parts) != 1:
        raise ContractError("keyword must be a single safe path component")
    if not run_id or not topic_id:
        raise ContractError("run_id and topic_id are required")
    _ = aware_datetime(created_at, "created_at")
    final_path = root / "final" / f"{keyword}.md"
    asset_dir = root / "assets" / keyword
    thumbnail = _thumbnail(asset_dir)
    body_paths = _body_paths(root, final_path, thumbnail)
    entries = [
        _record(root, "final_markdown", 1, final_path),
        _record(root, "naver_layout", 2, root / "final" / f"{keyword}-naver-layout.md"),
        _record(root, "naver_copy", 3, root / "final" / f"{keyword}-naver-copy.md"),
        _record(root, "image_map", 4, asset_dir / "image-map.md"),
    ]
    entries.extend(
        _record(root, "body_image", 5 + index, path)
        for index, path in enumerate(body_paths)
    )
    entries.append(_record(root, "thumbnail", 5 + len(body_paths), thumbnail))
    data: JSONMap = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "run_id": run_id,
        "topic_id": topic_id,
        "mode": mode,
        "created_at": created_at,
        "files": [entry.as_json() for entry in entries],
    }
    data["artifact_digest"] = _canonical_digest(data)
    try:
        validate_instance(data, SCHEMA_PATH)
    except SchemaError as error:
        raise ContractError(
            f"manifest does not match workflow schema: {error}"
        ) from error
    return data


def verify_manifest(root: Path, manifest_path: Path) -> Manifest:
    data = load_json_map(manifest_path)
    try:
        validate_instance(data, SCHEMA_PATH)
    except SchemaError as error:
        raise ContractError(
            f"manifest does not match workflow schema: {error}"
        ) from error
    manifest = _manifest_from_json(data)
    if _canonical_digest(data) != manifest.artifact_digest:
        raise ContractError("artifact_digest does not match the canonical manifest")
    root_resolved = root.resolve()
    for entry in manifest.files:
        path = (root / entry.path).resolve()
        try:
            _ = path.relative_to(root_resolved)
        except ValueError as error:
            raise ContractError(
                f"manifest path escapes workspace: {entry.path}"
            ) from error
        if not path.is_file():
            raise ContractError(f"manifest artifact is missing: {entry.path}")
        raw = path.read_bytes()
        if (
            len(raw) != entry.size_bytes
            or hashlib.sha256(raw).hexdigest() != entry.sha256
        ):
            raise ContractError(f"manifest artifact changed: {entry.path}")
    return manifest


__all__ = ["Manifest", "ManifestFile", "build_manifest", "verify_manifest"]
