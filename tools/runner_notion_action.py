from __future__ import annotations

import re
from pathlib import Path

from tools.contract_types import ContractError
from tools.external_adapter import ExternalSystem, ExternalWriteRequest
from tools.notion_resume import NotionQ2Failure
from tools.runner_state import state_paths
from tools.runner_types import (
    RunnerRequest,
    RunStatus,
    StageExecution,
    StageResult,
    StageRunContext,
)


def configured_notion_target(root: Path) -> str:
    config = root / "notion-config.md"
    if not config.is_file():
        raise ContractError(f"Notion config is missing: {config}")
    match = re.search(
        r"데이터 소스 ID:\s*`([^`]+)`", config.read_text(encoding="utf-8")
    )
    if match is None:
        raise ContractError("Notion data source ID is missing")
    return match.group(1)


def _pinned_notion_target(request: RunnerRequest) -> str:
    target_id = request.notion_target_id
    if target_id is None:
        raise ContractError("live Notion write requires a pinned Notion target")
    if target_id != configured_notion_target(request.root):
        raise ContractError("pinned Notion target changed in notion-config.md")
    return target_id


def notion_stage_action(context: StageRunContext) -> StageResult:
    request = context.request
    adapter = request.notion_adapter
    if adapter is None:
        raise ContractError("Notion adapter is not configured")
    target_id = _pinned_notion_target(request)
    manifest_path = request.root / "manifests" / f"{context.run_id}-workflow-manifest.json"
    checkpoint_root = request.state_dir or request.root / ".automation"
    try:
        result = adapter.write_and_verify(
            ExternalWriteRequest(
                root=request.root,
                manifest_path=manifest_path,
                run_log=state_paths(request.root, context.run_id, request.state_dir)[1],
                system=ExternalSystem.NOTION,
                gate="notion_write",
                run_id=context.run_id,
                target_id=target_id,
                dry_run=request.dry_run,
                checkpoint_path=checkpoint_root / "notion" / f"{context.run_id}.json",
            )
        )
    except NotionQ2Failure as error:
        return StageResult(
            RunStatus.FAILED,
            StageExecution.ATTEMPTED,
            str(error),
            details={
                "storage_integrity": "failed",
                "notion_target_id": target_id,
                "external_call": True,
            },
        )
    page_id = result.get("notion_page_id")
    verified_at = result.get("notion_last_verified_at")
    expected_digest = result.get("expected_notion_content_digest")
    content_digest = result.get("notion_content_digest")
    roundtrip = result.get("notion_roundtrip_digest")
    artifact_digest = result.get("artifact_digest")
    if not all(
        isinstance(value, str)
        for value in (
            page_id,
            verified_at,
            expected_digest,
            content_digest,
            roundtrip,
            artifact_digest,
        )
    ):
        raise ContractError("Notion adapter did not return Q2 identity")
    details = dict(result)
    details["notion_target_id"] = target_id
    details["external_call"] = True
    return StageResult(
        RunStatus.PASSED,
        StageExecution.PRODUCED,
        "Notion Q2 round-trip passed",
        (),
        None,
        RunStatus.READY_FOR_NAVER,
        details,
    )
