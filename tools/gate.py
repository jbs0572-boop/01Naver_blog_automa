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
    notion_connector: bool
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


def _matches_run(event: JSONMap, run_id: str) -> bool:
    if event.get("run_id") == run_id:
        return True
    run_ids = event.get("run_ids")
    return isinstance(run_ids, list) and run_id in run_ids


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


def _approval_events(
    events: list[JSONMap], gate: str, target_id: str, run_id: str
) -> list[tuple[datetime, JSONMap]]:
    approvals: list[tuple[datetime, JSONMap]] = []
    for event in events:
        if (
            event.get("event_type") == "approval"
            and event.get("gate") == gate
            and event.get("target_id") == target_id
            and _matches_run(event, run_id)
        ):
            approvals.append(
                (
                    parse_aware_datetime(
                        event.get("decided_at"), "approval.decided_at"
                    ),
                    event,
                )
            )
    approvals.sort(key=lambda item: item[0])
    return approvals


def _ensure_not_expired(event: JSONMap, now: datetime) -> None:
    expires_at = event.get("expires_at")
    if expires_at is None:
        return
    if parse_aware_datetime(expires_at, "approval.expires_at") < now:
        raise ContractError("latest matching approval has expired")


def batch_artifact_digest(event: JSONMap, run_id: str) -> str:
    run_ids = event.get("run_ids")
    max_items = event.get("max_items")
    if (
        not isinstance(run_ids, list)
        or any(not isinstance(item, str) for item in run_ids)
        or len(set(run_ids)) != len(run_ids)
        or isinstance(max_items, bool)
        or not isinstance(max_items, int)
        or max_items < len(run_ids)
    ):
        raise ContractError("batch approval run_ids or max_items is invalid")
    if run_id not in run_ids:
        raise ContractError("current run_id is outside the batch approval list")
    per_run = event.get("per_run_artifact_digests")
    if not isinstance(per_run, dict):
        raise ContractError("batch approval is missing per_run_artifact_digests")
    if set(per_run) != set(run_ids):
        raise ContractError("batch approval digest map does not exactly match run_ids")
    digest = per_run.get(run_id)
    if not isinstance(digest, str):
        raise ContractError("batch approval digest for current run is missing")
    return digest


def _verify_identity(
    event: JSONMap,
    manifest: Manifest,
    target_id: str,
    notion_page_id: str | None,
    notion_verified_at: str | None,
    blog_id: str | None,
) -> None:
    if not isinstance(notion_verified_at, str):
        raise ContractError("Gate B verification timestamp is missing")
    _ = parse_aware_datetime(notion_verified_at, "Gate B notion_verified_at")
    _ = parse_aware_datetime(
        event.get("notion_last_verified_at"), "approval.notion_last_verified_at"
    )
    if (
        target_id != blog_id
        or event.get("notion_page_id") != notion_page_id
        or event.get("notion_last_verified_at") != notion_verified_at
        or event.get("blog_id") != blog_id
        or event.get("notion_roundtrip_digest") != manifest.artifact_digest
    ):
        raise ContractError(
            "Gate B Notion page, verification time, blog id, or round-trip digest does not match"
        )


def verify_gate(
    root: Path,
    manifest_path: Path,
    run_log: Path,
    gate: str,
    run_id: str,
    target_id: str,
    *,
    notion_page_id: str | None = None,
    notion_verified_at: str | None = None,
    blog_id: str | None = None,
) -> JSONMap:
    manifest = verify_manifest(root, manifest_path)
    _ensure_manifest_run(manifest, run_id)
    if gate not in {"notion_write", "naver_draft_save"}:
        raise ContractError(f"unsupported gate: {gate}")
    if gate == "naver_draft_save" and manifest.mode != "formal":
        raise ContractError("Gate B is only valid in formal mode")
    if gate == "notion_write" and target_id != _configured_data_source_id(root):
        raise ContractError(
            "Gate A target_id does not match notion-config.md data source ID"
        )
    events = read_events(run_log)
    if not _has_q1_pass(events, manifest):
        raise ContractError("content-assembler Q1 pass is missing")
    if gate == "naver_draft_save" and not any(
        event.get("run_id") == run_id
        and event.get("stage") == "notion-rider"
        and _passed(event)
        for event in events
    ):
        raise ContractError("Notion Q2 pass is missing before Gate B")
    approvals = _approval_events(events, gate, target_id, run_id)
    if not approvals:
        raise ContractError("approval event with matching target and run is missing")
    _, approval = approvals[-1]
    if approval.get("decision") != "approved":
        raise ContractError("latest matching approval is not approved")
    _ensure_not_expired(approval, datetime.now(UTC))
    artifact_digest: JSONValue = approval.get("artifact_digest")
    if approval.get("scope") == "batch":
        artifact_digest = batch_artifact_digest(approval, run_id)
    if artifact_digest != manifest.artifact_digest:
        raise ContractError(
            "approval artifact_digest does not match the current manifest"
        )
    if gate == "naver_draft_save":
        _verify_identity(
            approval, manifest, target_id, notion_page_id, notion_verified_at, blog_id
        )
    result = dict(approval)
    result["verified_artifact_digest"] = manifest.artifact_digest
    return result


def authorize_external_write(request: GateRequest) -> JSONMap:
    manifest = verify_manifest(request.root, request.manifest_path)
    _ensure_manifest_run(manifest, request.run_id)
    beta_notion_write = manifest.mode == "beta" and request.gate == "notion_write"
    if not beta_notion_write:
        return verify_gate(
            root=request.root,
            manifest_path=request.manifest_path,
            run_log=request.run_log,
            gate=request.gate,
            run_id=request.run_id,
            target_id=request.target_id,
            notion_page_id=request.notion_page_id,
            notion_verified_at=request.notion_verified_at,
            blog_id=request.blog_id,
        )
    if not request.notion_connector:
        raise ContractError(
            "beta approval-free writes are limited to the Notion connector"
        )
    if request.notion_operation not in {
        "create_attachment",
        "create_page",
        "create_pages",
    }:
        raise ContractError("Notion operation is not allowed without Gate A")
    if (
        request.notion_operation != "create_attachment"
        and request.notion_resource_id != request.target_id
    ):
        raise ContractError("beta Notion write target does not match target_id")
    if request.target_id != _configured_data_source_id(request.root):
        raise ContractError(
            "beta Notion target_id does not match notion-config.md data source ID"
        )
    events = read_events(request.run_log)
    if not _has_q1_pass(events, manifest):
        raise ContractError("content-assembler Q1 pass is missing")
    return {
        "gate": "notion_write",
        "decision": "not_required",
        "scope": "beta",
        "target_id": request.target_id,
        "verified_artifact_digest": manifest.artifact_digest,
    }


__all__ = [
    "GateRequest",
    "authorize_external_write",
    "batch_artifact_digest",
    "parse_aware_datetime",
    "verify_gate",
]
