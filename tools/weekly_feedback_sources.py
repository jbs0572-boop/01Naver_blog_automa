from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.feedback_manifest import safe_read
from tools.topic_feedback_models import compute_digest, parse_artifact
from tools.topic_metadata import CreatorAdvisorSnapshot, read_snapshot
from tools.topic_performance_input import parse_kst_timestamp


def read_json(path: Path) -> JSONMap:
    try:
        value: JSONValue = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("feedback input digest mismatch") from error
    if not isinstance(value, dict):
        raise ContractError("feedback input digest mismatch")
    return value


def artifact_paths(root: Path, digests: frozenset[str]) -> tuple[Path, ...]:
    selected: list[Path] = []
    for directory in ("publication-links", "blog-stats"):
        base = root / "metadata" / directory
        if not base.exists():
            continue
        if base.is_symlink() or not base.is_dir():
            raise ContractError("feedback evidence path is unsafe")
        for path in sorted(base.rglob("*.json")):
            _ = safe_read(root, path)
            payload = read_json(path)
            if payload.get("digest") in digests:
                _ = parse_artifact(payload)
                if payload.get("digest") != compute_digest(payload):
                    raise ContractError("feedback input digest mismatch")
                selected.append(path)
    return tuple(selected)


def latest_creator(
    root: Path, as_of: datetime
) -> tuple[Path, CreatorAdvisorSnapshot] | None:
    base = root / "metadata" / "creator-advisor"
    if not base.exists():
        return None
    if base.is_symlink() or not base.is_dir():
        raise ContractError("feedback evidence path is unsafe")
    candidates: list[tuple[datetime, str, Path, CreatorAdvisorSnapshot]] = []
    for path in sorted(base.rglob("*.json")):
        _ = safe_read(root, path)
        raw = read_json(path)
        capture_id, date_value = raw.get("capture_id"), raw.get("as_of_date")
        if not isinstance(capture_id, str) or not isinstance(date_value, str):
            raise ContractError("feedback input digest mismatch")
        snapshot = read_snapshot(
            path,
            expected_capture_id=capture_id,
            expected_as_of_date=date_value,
        )
        captured = parse_kst_timestamp(snapshot.captured_at, "captured_at")
        if captured <= as_of:
            candidates.append((captured, capture_id, path, snapshot))
    if not candidates:
        return None
    _, _, path, snapshot = max(candidates)
    return path, snapshot


def source_freshness(
    root: Path, inputs: tuple[Path, ...], data_as_of: datetime
) -> list[JSONValue]:
    output: list[JSONValue] = []
    for path in inputs:
        payload = read_json(path)
        captured = payload.get("captured_at")
        age_days: int | None = None
        if isinstance(captured, str):
            age_days = max(
                (data_as_of - parse_kst_timestamp(captured, "captured_at")).days,
                0,
            )
        output.append(
            {
                "path": path.relative_to(root).as_posix(),
                "schema_version": payload.get("schema_version"),
                "captured_at": captured,
                "age_days": age_days,
            }
        )
    return output


__all__ = ["artifact_paths", "latest_creator", "source_freshness"]
