from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_models import (
    compute_digest,
    parse_artifact,
    serialize_artifact,
)
from tools.topic_feedback_provenance import ProvenanceRequest, validate_provenance_file
from tools.weekly_feedback_bundle_store import publish_bundle_at

KST: Final = timedelta(hours=9)
type BundleFault = Callable[[int, str], None]


@dataclass(frozen=True, slots=True)
class EvidenceFile:
    path: str
    size_bytes: int
    sha256: str
    schema_version: str

    def as_json(self) -> JSONMap:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class EvidenceManifestRequest:
    root: Path
    feedback_id: str
    created_at: str
    data_as_of: str
    inputs: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    encoded: bytes
    digest: str
    files: tuple[EvidenceFile, ...]


@dataclass(frozen=True, slots=True)
class BundleOutput:
    relative_path: Path
    encoded: bytes


def canonical_json(payload: JSONMap) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as error:
        raise ContractError("feedback payload is invalid") from error


def sha256(encoded: bytes) -> str:
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def safe_read(root: Path, path: Path) -> tuple[Path, bytes]:
    try:
        root_resolved = root.resolve(strict=True)
        relative = path.relative_to(root) if path.is_absolute() else path
    except (OSError, ValueError) as error:
        raise ContractError("feedback evidence path is unsafe") from error
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ContractError("feedback evidence path is unsafe")
    current = root_resolved
    for part in relative.parts[:-1]:
        current /= part
        try:
            info = current.lstat()
        except OSError as error:
            raise ContractError("feedback evidence path is unsafe") from error
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ContractError("feedback evidence path is unsafe")
    try:
        descriptor = os.open(
            root_resolved / relative,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise ContractError("feedback evidence path is unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            return relative, handle.read()
    except OSError as error:
        raise ContractError("feedback evidence path is unsafe") from error


def _validated_file(root: Path, path: Path, cutoff: datetime) -> EvidenceFile:
    relative, encoded = safe_read(root, path)
    try:
        value: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("feedback input digest mismatch") from error
    if not isinstance(value, dict):
        raise ContractError("feedback input digest mismatch")
    schema = value.get("schema_version")
    if not isinstance(schema, str):
        raise ContractError("feedback input digest mismatch")
    if not validate_provenance_file(
        ProvenanceRequest(root, str(relative), value, cutoff)
    ):
        try:
            artifact = parse_artifact(value)
            expected = serialize_artifact(artifact).encode() + b"\n"
        except ContractError as error:
            raise ContractError("feedback input digest mismatch") from error
        if encoded != expected or value.get("digest") != compute_digest(value):
            raise ContractError("feedback input digest mismatch")
    return EvidenceFile(str(relative), len(encoded), sha256(encoded), schema)


def build_evidence_manifest(request: EvidenceManifestRequest) -> EvidenceManifest:
    try:
        cutoff = datetime.fromisoformat(request.data_as_of)
    except ValueError as error:
        raise ContractError("data_as_of must be a KST timestamp") from error
    if (
        not request.data_as_of.endswith("+09:00")
        or not request.created_at.endswith("+09:00")
        or cutoff.utcoffset() != KST
    ):
        raise ContractError("feedback manifest timestamps must be KST")
    try:
        created_at = datetime.fromisoformat(request.created_at)
    except ValueError as error:
        raise ContractError("feedback manifest timestamps must be KST") from error
    if created_at.utcoffset() != KST:
        raise ContractError("feedback manifest timestamps must be KST")
    files = tuple(
        sorted(
            (_validated_file(request.root, path, cutoff) for path in request.inputs),
            key=lambda item: item.path,
        )
    )
    unsigned: JSONMap = {
        "schema_version": "feedback-evidence-manifest-v1",
        "feedback_id": request.feedback_id,
        "created_at": request.created_at,
        "data_as_of": request.data_as_of,
        "files": [item.as_json() for item in files],
    }
    digest = sha256(canonical_json(unsigned))
    payload = dict(unsigned)
    payload["digest"] = digest
    return EvidenceManifest(canonical_json(payload), digest, files)


def publish_bundle(
    root: Path,
    outputs: tuple[BundleOutput, ...],
    fault: BundleFault | None = None,
) -> None:
    if len({item.relative_path for item in outputs}) != len(outputs):
        raise ContractError("feedback bundle contains duplicate destinations")
    publish_bundle_at(root, outputs, fault)


__all__ = [
    "BundleFault",
    "BundleOutput",
    "EvidenceManifest",
    "EvidenceManifestRequest",
    "build_evidence_manifest",
    "canonical_json",
    "publish_bundle",
    "safe_read",
    "sha256",
]
