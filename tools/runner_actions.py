from __future__ import annotations

from pathlib import Path

from tools.contract_types import ContractError
from tools.log_contract import read_events
from tools.manifest import ManifestBuildInput, build_manifest, verify_manifest
from tools.runner_naver_action import naver_input_title, naver_stage_action
from tools.runner_notion_action import notion_stage_action
from tools.runner_stages import now
from tools.runner_state import atomic_write_json
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
                selection_context=request.selection_context,
                q1_feedback=context.q1_feedback,
            ))
            if stage == "content-assembler":
                manifest = verify_manifest(
                    request.root, _manifest_for(request, run_id, created_at)
                )
                return StageResult(
                    RunStatus.PASSED,
                    StageExecution.PRODUCED,
                    result.message or "content assembled and Q1 manifest validated",
                    result.artifacts,
                    result.resolved_keyword,
                    details={"artifact_digest": manifest.artifact_digest},
                )
            return result
        if stage == "content-assembler":
            manifest = verify_manifest(
                request.root, _manifest_for(request, run_id, created_at)
            )
            return StageResult(
                RunStatus.VALIDATED,
                StageExecution.VALIDATED,
                "canonical manifest validated; producer remains local",
                details={"artifact_digest": manifest.artifact_digest},
            )
        if stage == "notion-rider" and request.notion_adapter is not None:
            return notion_stage_action(context)
        if stage == "naver-rider" and request.naver_adapter is not None:
            return naver_stage_action(context)
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


__all__ = ["naver_input_title", "stage_action"]
