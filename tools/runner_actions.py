from __future__ import annotations

import re
from pathlib import Path

from tools.contract_types import ContractError
from tools.external_adapter import ExternalSystem, ExternalWriteRequest
from tools.log_contract import read_events
from tools.manifest import ManifestBuildInput, build_manifest, verify_manifest
from tools.runner_stages import now
from tools.runner_state import atomic_write_json, state_paths
from tools.runner_types import (
    JobName,
    RunnerBlocked,
    RunnerRequest,
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
    StageRunContext,
)
from tools.weekly_report import write_weekly_report


def _topic_id(request: RunnerRequest, run_id: str) -> str:
    return f"TOPIC-{request.keyword}" if request.keyword else f"TOPIC-{run_id}"


def _manifest_for(request: RunnerRequest, run_id: str, created_at: str) -> Path:
    if request.keyword is None:
        raise ContractError("manifest creation requires keyword")
    path = request.root / "manifests" / f"{run_id}-workflow-manifest.json"
    if path.is_file():
        manifest = verify_manifest(request.root, path)
        if (
            manifest.run_id != run_id
            or manifest.topic_id != f"TOPIC-{request.keyword}"
        ):
            raise ContractError("existing manifest identity does not match the run")
        return path
    data = build_manifest(
        ManifestBuildInput(
            request.root,
            request.keyword,
            run_id,
            f"TOPIC-{request.keyword}",
            created_at,
        )
    )
    atomic_write_json(path, data)
    _ = verify_manifest(request.root, path)
    return path


def _notion_target(root: Path) -> str:
    config = root / "notion-config.md"
    if not config.is_file():
        raise ContractError(f"Notion config is missing: {config}")
    match = re.search(
        r"데이터 소스 ID:\s*`([^`]+)`", config.read_text(encoding="utf-8")
    )
    if match is None:
        raise ContractError("Notion data source ID is missing")
    return match.group(1)


def stage_action(context: StageRunContext) -> StageResult:
    request = context.request
    job = context.job
    stage = context.stage
    run_id = context.run_id
    created_at = context.created_at
    if job is JobName.DAILY_GENERATE:
        if request.executor is not None and stage in {
            "topic-selector",
            "researcher",
            "writer",
            "image-maker",
            "content-assembler",
        }:
            result = request.executor.execute(StageExecutionContext(
                root=request.root,
                stage=stage,
                run_id=run_id,
                topic_id=_topic_id(request, run_id),
                keyword=request.keyword,
                work_dir=request.root / ".automation" / "work" / run_id / stage,
                q1_feedback=context.q1_feedback,
            ))
            if stage == "content-assembler":
                _ = _manifest_for(request, run_id, created_at)
                return StageResult(
                    RunStatus.PASSED,
                    StageExecution.PRODUCED,
                    result.message or "content assembled and Q1 manifest validated",
                    result.artifacts,
                    result.resolved_keyword,
                )
            return result
        if stage == "content-assembler":
            _ = _manifest_for(request, run_id, created_at)
            return StageResult(
                RunStatus.VALIDATED,
                StageExecution.VALIDATED,
                "canonical manifest validated; producer remains local",
            )
        if stage == "notion-rider" and request.notion_adapter is not None:
            manifest_path = (
                request.root / "manifests" / f"{run_id}-workflow-manifest.json"
            )
            result = request.notion_adapter.write_and_verify(
                ExternalWriteRequest(
                    root=request.root,
                    manifest_path=manifest_path,
                    run_log=state_paths(request.root, run_id, request.state_dir)[1],
                    system=ExternalSystem.NOTION,
                    gate="notion_write",
                    run_id=run_id,
                    target_id=_notion_target(request.root),
                    dry_run=request.dry_run,
                )
            )
            page_id = result.get("notion_page_id")
            verified_at = result.get("notion_last_verified_at")
            roundtrip = result.get("notion_roundtrip_digest")
            if not all(
                isinstance(value, str) for value in (page_id, verified_at, roundtrip)
            ):
                raise ContractError("Notion adapter did not return Q2 identity")
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                "Notion Q2 round-trip passed",
                (),
                None,
                RunStatus.READY_FOR_NAVER,
                result,
            )
        if stage == "naver-rider" and request.naver_adapter is not None:
            if request.keyword is None:
                raise ContractError("Naver input requires a resolved keyword")
            input_path = request.root / "final" / f"{request.keyword}-naver-input.md"
            if not input_path.is_file():
                raise ContractError(f"Naver input is missing: {input_path}")
            body = input_path.read_text(encoding="utf-8")
            title = next(
                (
                    line[2:].strip()
                    for line in body.splitlines()
                    if line.startswith("# ")
                ),
                request.keyword,
            )
            manifest_path = (
                request.root / "manifests" / f"{run_id}-workflow-manifest.json"
            )
            manifest = verify_manifest(request.root, manifest_path)
            if not request.confirmed:
                preview = request.naver_adapter.prepare(
                    title, body, manifest.artifact_digest
                )
                details = dict(preview)
                details["confirmation_requested_at"] = now(request).isoformat()
                details["naver_title"] = title
                return StageResult(
                    RunStatus.PASSED,
                    StageExecution.PRODUCED,
                    "awaiting_user_confirmation",
                    (),
                    None,
                    RunStatus.AWAITING_USER_CONFIRMATION,
                    details,
                )
            saved = request.naver_adapter.save(title, manifest.artifact_digest)
            if saved.get("draft_status") != "saved":
                raise ContractError("Naver adapter did not confirm draft save")
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                "draft_saved",
                (),
                None,
                RunStatus.DRAFT_SAVED,
                saved,
            )
        if stage in {"notion-rider", "naver-rider"}:
            return StageResult(
                RunStatus.SKIPPED,
                StageExecution.NOT_CALLED,
                f"{stage} external storage is pending; adapter is not configured",
            )
        return StageResult(
            RunStatus.SKIPPED,
            StageExecution.NOT_CALLED,
            "producer remains local; stage not executed",
        )
    if job is JobName.WEEKLY_IMPROVE:
        if stage != "researcher":
            return StageResult(
                RunStatus.SKIPPED,
                StageExecution.NOT_CALLED,
                "weekly-improve is read-only aggregation",
            )
        log_paths = sorted((request.root / "runs").glob("*.jsonl"))
        event_count = sum(len(read_events(path)) for path in log_paths)
        artifact_count = sum(
            1
            for directory in ("research", "drafts", "final", "assets")
            for path in sorted((request.root / directory).rglob("*"))
            if path.is_file()
        )
        return StageResult(
            RunStatus.PASSED,
            StageExecution.AGGREGATED,
            (
                f"read-only improvement inputs aggregated: {event_count} log events, "
                f"{artifact_count} artifacts; report={write_weekly_report(request.root, now(request))}"
            ),
        )
    if stage != "naver-rider":
        return StageResult(
            RunStatus.SKIPPED,
            StageExecution.NOT_CALLED,
            "naver-publish does not execute upstream stages",
        )
    manifest_path = request.root / "manifests" / f"{run_id}-workflow-manifest.json"
    if manifest_path.is_file():
        _ = verify_manifest(request.root, manifest_path)
    if not request.dry_run:
        raise RunnerBlocked(
            "naver-publish requires --dry-run; external writes are disabled"
        )
    raise RunnerBlocked(
        "Naver write requires Q1/Q2 verification and final user confirmation; dry-run made no external call"
    )
