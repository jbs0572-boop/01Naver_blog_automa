from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_manifest_types import (
    FeedbackEvidence,
    FeedbackEvidenceRequest,
)
from tools.topic_feedback_models import TopicSignalSnapshot, parse_artifact
from tools.topic_feedback_policy import load_registry
from tools.topic_feedback_provenance import ProvenanceRequest, validate_provenance_file
from tools.topic_feedback_scoring_models import AuxiliarySignal

SOURCE_REGISTRY: Final = (
    Path(__file__).resolve().parents[1] / "config/topic-feedback-sources.json"
)
_MANIFEST_FIELDS: Final = frozenset(
    {"schema_version", "feedback_id", "created_at", "data_as_of", "files", "digest"}
)
_FILE_FIELDS: Final = frozenset({"path", "size_bytes", "sha256", "schema_version"})


def _canonical(value: JSONMap) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as error:
        raise ContractError("feedback evidence manifest is invalid") from error


def _sha(encoded: bytes) -> str:
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _time(value: JSONValue, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("+09:00"):
        raise ContractError(f"{label} must be a KST timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{label} must be a KST timestamp") from error
    if parsed.utcoffset() != timedelta(hours=9):
        raise ContractError(f"{label} must be a KST timestamp")
    return parsed


def _read(root: Path, path: Path) -> bytes:
    try:
        resolved_root = root.resolve(strict=True)
        relative = path.relative_to(root) if path.is_absolute() else path
    except (OSError, ValueError) as error:
        raise ContractError("feedback evidence path is unsafe") from error
    if relative.is_absolute() or ".." in relative.parts:
        raise ContractError("feedback evidence path is unsafe")
    current = resolved_root
    for part in relative.parts[:-1]:
        current /= part
        try:
            info = current.lstat()
        except OSError as error:
            raise ContractError("feedback evidence path is unsafe") from error
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ContractError("feedback evidence path is unsafe")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(resolved_root / relative, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise ContractError("feedback evidence path is unsafe")
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()
    except OSError as error:
        raise ContractError("feedback evidence path is unsafe") from error


def _map(encoded: bytes, label: str) -> JSONMap:
    try:
        value: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is invalid") from error
    if not isinstance(value, dict):
        raise ContractError(f"{label} is invalid")
    return value


def _signals(
    payload: JSONMap,
    confidence: str,
) -> tuple[AuxiliarySignal, ...]:
    derived = payload.get("derived")
    rankings = derived.get("ranking_features") if isinstance(derived, dict) else None
    if rankings is None:
        return ()
    if not isinstance(rankings, list):
        raise ContractError("feedback ranking features are invalid")
    output: list[AuxiliarySignal] = []
    for raw in rankings:
        if not isinstance(raw, dict):
            raise ContractError("feedback ranking features are invalid")
        keyword, unit, value = raw.get("keyword"), raw.get("unit"), raw.get("value")
        if (
            not isinstance(keyword, str)
            or not isinstance(unit, str)
            or isinstance(value, bool)
            or not isinstance(value, int | float)
        ):
            raise ContractError("feedback ranking features are invalid")
        output.append(
            AuxiliarySignal(
                keyword,
                str(payload["source_id"]),
                confidence,
                str(payload["as_of_date"]),
                unit,
                float(value),
            )
        )
    return tuple(output)


def load_feedback_evidence(request: FeedbackEvidenceRequest) -> FeedbackEvidence:
    manifest_bytes = _read(request.root, request.path)
    manifest = _map(manifest_bytes, "feedback evidence manifest")
    unsigned = dict(manifest)
    digest = unsigned.pop("digest", None)
    if (
        frozenset(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema_version") != "feedback-evidence-manifest-v1"
        or digest != request.expected_digest
        or digest != _sha(_canonical(unsigned))
        or _canonical(manifest) != manifest_bytes.rstrip(b"\n")
    ):
        raise ContractError("feedback evidence manifest is invalid")
    cutoff = _time(request.evaluation_as_of, "evaluation_as_of")
    created_at = _time(manifest.get("created_at"), "created_at")
    data_as_of = _time(manifest.get("data_as_of"), "data_as_of")
    if data_as_of > cutoff or created_at > cutoff:
        raise ContractError("feedback evidence manifest is from the future")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ContractError("feedback evidence manifest is invalid")
    registry = load_registry(SOURCE_REGISTRY)
    policies = {item.source_id: item for item in registry.sources}
    signals: list[AuxiliarySignal] = []
    signal_paths: list[str] = []
    for item in files:
        if not isinstance(item, dict) or frozenset(item) != _FILE_FIELDS:
            raise ContractError("feedback evidence manifest is invalid")
        relative = item.get("path")
        if not isinstance(relative, str):
            raise ContractError("feedback evidence manifest is invalid")
        encoded = _read(request.root, Path(relative))
        if len(encoded) != item.get("size_bytes") or _sha(encoded) != item.get(
            "sha256"
        ):
            raise ContractError("feedback evidence file mismatch")
        payload = _map(encoded, "feedback evidence file")
        if payload.get("schema_version") != item.get("schema_version"):
            raise ContractError("feedback evidence file mismatch")
        if validate_provenance_file(
            ProvenanceRequest(request.root, relative, payload, cutoff)
        ):
            continue
        artifact = parse_artifact(payload)
        if not isinstance(artifact, TopicSignalSnapshot):
            continue
        source_id = payload.get("source_id")
        policy = policies.get(source_id) if isinstance(source_id, str) else None
        if (
            policy is None
            or source_id not in request.enabled_sources
            or not policy.enabled
            or policy.confidence.value not in {"A", "B"}
            or "same_candidate_signal" not in policy.allowed_purposes
            or payload.get("source_confidence") != policy.confidence.value
            or payload.get("access_mode") != policy.access_mode.value
        ):
            continue
        accepted = _signals(payload, policy.confidence.value)
        signals.extend(accepted)
        if accepted:
            signal_paths.append(relative)
    return FeedbackEvidence(
        str(digest),
        str(manifest["data_as_of"]),
        tuple(signals),
        tuple(signal_paths),
        request.path.relative_to(request.root).as_posix(),
    )


def load_feedback_path(
    root: Path,
    path: Path,
    evaluation_as_of: str,
    enabled_sources: tuple[str, ...],
) -> FeedbackEvidence:
    manifest = _map(_read(root, path), "feedback evidence manifest")
    digest = manifest.get("digest")
    if not isinstance(digest, str):
        raise ContractError("feedback evidence manifest is invalid")
    return load_feedback_evidence(
        FeedbackEvidenceRequest(root, path, digest, evaluation_as_of, enabled_sources)
    )


def latest_feedback_evidence(
    root: Path, evaluation_as_of: str, enabled_sources: tuple[str, ...]
) -> FeedbackEvidence | None:
    directory = root / "metadata/feedback-manifests"
    if not directory.exists():
        return None
    if directory.is_symlink() or not directory.is_dir():
        raise ContractError("feedback evidence path is unsafe")
    candidates: list[tuple[datetime, str, Path, str]] = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ContractError("feedback evidence path is unsafe")
        manifest = _map(_read(root, path), "feedback evidence manifest")
        digest = manifest.get("digest")
        feedback_id = manifest.get("feedback_id")
        if not isinstance(digest, str) or not isinstance(feedback_id, str):
            raise ContractError("feedback evidence manifest is invalid")
        data_as_of = _time(manifest.get("data_as_of"), "data_as_of")
        if data_as_of <= _time(evaluation_as_of, "evaluation_as_of"):
            candidates.append((data_as_of, feedback_id, path, digest))
    if not candidates:
        return None
    _, _, path, digest = max(candidates)
    return load_feedback_evidence(
        FeedbackEvidenceRequest(root, path, digest, evaluation_as_of, enabled_sources)
    )


def feedback_evidence_by_digest(
    root: Path,
    digest: str,
    evaluation_as_of: str,
    enabled_sources: tuple[str, ...],
) -> FeedbackEvidence:
    directory = root / "metadata/feedback-manifests"
    if directory.is_symlink() or not directory.is_dir():
        raise ContractError("pinned feedback evidence is unavailable")
    matches: list[Path] = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ContractError("feedback evidence path is unsafe")
        manifest = _map(_read(root, path), "feedback evidence manifest")
        if manifest.get("digest") == digest:
            matches.append(path)
    if len(matches) != 1:
        raise ContractError("pinned feedback evidence is unavailable")
    return load_feedback_evidence(
        FeedbackEvidenceRequest(
            root, matches[0], digest, evaluation_as_of, enabled_sources
        )
    )
