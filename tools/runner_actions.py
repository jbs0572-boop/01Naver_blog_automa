from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from tools.contract_types import ContractError
from tools.log_contract import read_events
from tools.manifest import ManifestBuildInput, build_manifest, verify_manifest
from tools.notion_content import parse_naver_copy
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
from tools.weekly_feedback import WeeklyFeedbackRequest, generate_weekly_feedback


def _topic_id(request: RunnerRequest, run_id: str) -> str:
    return f"TOPIC-{request.keyword}" if request.keyword else f"TOPIC-{run_id}"


def _manifest_for(
    request: RunnerRequest,
    run_id: str,
    created_at: str,
    *,
    rebuild: bool = False,
) -> Path:
    if request.keyword is None:
        raise ContractError("manifest creation requires keyword")
    path = request.root / "manifests" / f"{run_id}-workflow-manifest.json"
    if path.is_file() and not rebuild:
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
                q1_preflight_codes=context.q1_preflight_codes,
                stage_attempt=context.stage_attempt,
                model_config=request.model_config,
            ))
            if stage == "content-assembler":
                for suffix in ("-naver-copy.md", "-naver-input.md"):
                    copy_path = (
                        request.root / "final" / f"{request.keyword}{suffix}"
                    )
                    _ = parse_naver_copy(copy_path)
                manifest = verify_manifest(
                    request.root,
                    _manifest_for(
                        request,
                        run_id,
                        created_at,
                        rebuild=True,
                    ),
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
                f"{stage} external storage is pending for the external continuation phase",
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
        bundle = generate_weekly_feedback(
            WeeklyFeedbackRequest(
                request.root,
                now(request)
                .astimezone(ZoneInfo("Asia/Seoul"))
                .isoformat(timespec="seconds"),
            )
        )
        return StageResult(
            RunStatus.PASSED,
            StageExecution.AGGREGATED,
            (
                f"read-only improvement inputs aggregated: {event_count} log events, "
                f"{artifact_count} artifacts; report={bundle.operational_report}; "
                f"feedback={bundle.feedback_json}; manifest={bundle.manifest}"
            ),
            details={
                "weekly_operational_report_path": str(bundle.operational_report),
                "weekly_feedback_json_path": str(bundle.feedback_json),
                "weekly_feedback_markdown_path": str(bundle.feedback_markdown),
                "weekly_feedback_digest": bundle.feedback_digest,
                "weekly_feedback_manifest_path": str(bundle.manifest),
                "weekly_feedback_manifest_digest": bundle.manifest_digest,
                "weekly_external_call_count": 0,
            },
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
        "Naver draft save is dashboard-only after Q1/Q2/Q3 Gate verification; "
        + "naver-publish dry-run made no external call"
    )


__all__ = ["naver_input_title", "stage_action"]
