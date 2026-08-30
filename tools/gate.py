from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.log_contract import read_events
from tools.manifest import Manifest, verify_manifest


@dataclass(frozen=True, slots=True)
class GateRequest:
    root: Path
    manifest_path: Path
    run_log: Path
    gate: str
    run_id: str
    target_id: str
    notion_connector: bool = False
    notion_operation: str | None = None
    notion_resource_id: str | None = None
    notion_page_id: str | None = None
    notion_verified_at: str | None = None
    blog_id: str | None = None


def parse_aware_datetime(value: JSONValue, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{field} is not a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _passed(event: JSONMap) -> bool:
    return event.get("status") in {"passed", "success", "completed"}


def _ensure_manifest_run(manifest: Manifest, run_id: str) -> None:
    if manifest.run_id != run_id:
        raise ContractError("manifest run_id does not match workflow run_id")


def _has_q1_pass(events: list[JSONMap], manifest: Manifest) -> bool:
    return any(
        event.get("run_id") == manifest.run_id
        and event.get("topic_id") == manifest.topic_id
        and event.get("stage") == "content-assembler"
        and _passed(event)
        for event in events
    )


def _configured_data_source_id(root: Path) -> str:
    config_path = root / "notion-config.md"
    if not config_path.is_file():
        raise ContractError(f"Notion config is missing: {config_path}")
    match = re.search(
        r"데이터 소스 ID:\s*`([^`]+)`", config_path.read_text(encoding="utf-8")
    )
    if match is None:
        raise ContractError("Notion data source ID is missing from notion-config.md")
    return match.group(1)


def _q2_quality(events: list[JSONMap], manifest: Manifest) -> JSONMap:
    for event in reversed(events):
        if (
            event.get("run_id") == manifest.run_id
            and event.get("topic_id") == manifest.topic_id
            and event.get("stage") == "notion-rider"
            and _passed(event)
        ):
            quality = event.get("quality")
            if isinstance(quality, dict):
                return quality
    raise ContractError("Notion Q2 identity details are missing")


def _verify_q2_identity(
    events: list[JSONMap],
    manifest: Manifest,
    request: GateRequest,
) -> None:
    notion_page_id = request.notion_page_id
    notion_verified_at = request.notion_verified_at
    blog_id = request.blog_id
    if not isinstance(notion_page_id, str):
        raise ContractError("Notion page ID is missing before Naver write")
    if not isinstance(notion_verified_at, str):
        raise ContractError(
            "Notion verification timestamp is missing before Naver write"
        )
    if not isinstance(blog_id, str):
        raise ContractError("Naver blog ID is missing before Naver write")
    quality = _q2_quality(events, manifest)
    expected_page_id = quality.get("notion_page_id")
    expected_verified_at = quality.get("notion_last_verified_at")
    expected_digest = quality.get("notion_roundtrip_digest")
    if not isinstance(expected_page_id, str) or not isinstance(
        expected_verified_at, str
    ):
        raise ContractError("Notion Q2 identity details are incomplete")
    if not isinstance(expected_digest, str):
        raise ContractError("Notion Q2 round-trip digest is missing")
    verified_at = parse_aware_datetime(notion_verified_at, "notion_verified_at")
    expected_verified = parse_aware_datetime(
        expected_verified_at, "notion_last_verified_at"
    )
    if (
        request.target_id != blog_id
        or notion_page_id != expected_page_id
        or verified_at < expected_verified
        or expected_digest != manifest.artifact_digest
    ):
        raise ContractError(
            "Notion Q2 page, verification time, blog id, or round-trip digest does not match"
        )


def verify_gate(request: GateRequest) -> JSONMap:
    manifest = verify_manifest(request.root, request.manifest_path)
    _ensure_manifest_run(manifest, request.run_id)
    if request.gate not in {"notion_write", "naver_draft_save"}:
        raise ContractError(f"unsupported gate: {request.gate}")
    if request.gate == "notion_write" and request.target_id != _configured_data_source_id(request.root):
        raise ContractError(
            "Notion target_id does not match notion-config.md data source ID"
        )
    events = read_events(request.run_log)
    if not _has_q1_pass(events, manifest):
        raise ContractError("content-assembler Q1 pass is missing")
    if request.gate == "naver_draft_save" and not any(
        event.get("run_id") == request.run_id
        and event.get("stage") == "notion-rider"
        and _passed(event)
        for event in events
    ):
        raise ContractError("Notion Q2 pass is missing before Naver draft save")
    if request.gate == "naver_draft_save":
        _verify_q2_identity(events, manifest, request)
        return {
            "gate": request.gate,
            "decision": "not_required",
            "scope": "production",
            "target_id": request.target_id,
            "verified_artifact_digest": manifest.artifact_digest,
            "notion_page_id": request.notion_page_id,
            "notion_verified_at": request.notion_verified_at,
            "blog_id": request.blog_id,
        }
    return {
        "gate": request.gate,
        "decision": "not_required",
        "scope": "production",
        "target_id": request.target_id,
        "verified_artifact_digest": manifest.artifact_digest,
    }


def authorize_external_write(request: GateRequest) -> JSONMap:
    manifest = verify_manifest(request.root, request.manifest_path)
    _ensure_manifest_run(manifest, request.run_id)
    if request.gate != "notion_write":
        return verify_gate(request)
    if not request.notion_connector:
        raise ContractError("Notion writes are limited to the Notion connector")
    if request.notion_operation not in {
        "create_attachment",
        "create_page",
        "create_pages",
    }:
        raise ContractError("Notion operation is not allowed")
    if (
        request.notion_operation != "create_attachment"
        and request.notion_resource_id != request.target_id
    ):
        raise ContractError("Notion write target does not match target_id")
    return verify_gate(request)


__all__ = [
    "GateRequest",
    "authorize_external_write",
    "parse_aware_datetime",
    "verify_gate",
]
