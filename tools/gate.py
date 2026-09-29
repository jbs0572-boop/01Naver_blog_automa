from __future__ import annotations

import re
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.gate_models import GateRequest, parse_aware_datetime
from tools.log_contract import read_events
from tools.manifest import Manifest, verify_manifest
from tools.naver_adapter import load_naver_config
from tools.naver_gate import (
    NaverToolRequest,
    authorize_naver_target,
    canonical_naver_input,
    verify_article_quality,
    verify_naver_confirmation,
    verify_q2_identity,
)


def _passed(event: JSONMap) -> bool:
    return event.get("status") in {"passed", "success", "completed"}


def _ensure_manifest_run(manifest: Manifest, run_id: str) -> None:
    if manifest.run_id != run_id:
        raise ContractError("manifest run_id does not match workflow run_id")


def _verify_latest_q1(events: list[JSONMap], manifest: Manifest) -> None:
    for event in reversed(events):
        if (
            event.get("run_id") != manifest.run_id
            or event.get("topic_id") != manifest.topic_id
            or event.get("stage") != "content-assembler"
        ):
            continue
        quality = event.get("quality")
        if event.get("telemetry_version") != 2 or not _passed(event):
            raise ContractError("latest content-assembler Q1 result did not pass")
        if not isinstance(quality, dict):
            raise ContractError("latest content-assembler Q1 identity is missing")
        if quality.get("artifact_digest") != manifest.artifact_digest:
            raise ContractError(
                "latest content-assembler Q1 artifact digest does not match manifest"
            )
        return
    raise ContractError("latest content-assembler Q1 result is missing")


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
    _verify_latest_q1(events, manifest)
    if request.gate == "naver_draft_save":
        verify_q2_identity(
            events, manifest, request, _configured_data_source_id(request.root)
        )
        return {
            "gate": request.gate,
            "decision": "not_required",
            "scope": "production",
            "target_id": request.target_id,
            "verified_artifact_digest": manifest.artifact_digest,
            "notion_page_id": request.notion_page_id,
            "notion_verified_at": request.notion_verified_at,
            "blog_id": request.blog_id,
            **verify_article_quality(request.root, events, manifest),
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
    if request.gate == "naver_draft_save":
        config = load_naver_config(request.root / "naver-config.md")
        canonical = canonical_naver_input(manifest, request.root)
        authorize_naver_target(
            NaverToolRequest(
                request.target_id,
                request.blog_id,
                request.naver_connector,
                request.naver_operation,
                request.naver_url,
                request.naver_locator,
                request.naver_value,
                request.naver_phase,
            ),
            config,
            canonical,
        )
        result = verify_gate(request)
        if request.naver_phase == "save":
            verify_naver_confirmation(
                read_events(request.run_log), manifest, request.blog_id, canonical.title
            )
        return result
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
    if request.notion_resource_id != request.target_id:
        raise ContractError("Notion write target does not match target_id")
    return verify_gate(request)


__all__ = [
    "GateRequest",
    "authorize_external_write",
    "parse_aware_datetime",
    "verify_gate",
]
