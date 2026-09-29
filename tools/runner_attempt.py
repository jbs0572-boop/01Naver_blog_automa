from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from tools.article_quality import ArticleQualityFailure
from tools.codex_stage_error import StageExecutionError, StageFailureType
from tools.contract_types import ContractError, JSONMap
from tools.q1_feedback import Q1FailureCode, active_q1_codes
from tools.run_cancellation import read_cancellation
from tools.runner_actions import stage_action
from tools.runner_records import (
    next_stage_attempt,
    q1_attempts_used,
    q1_retry_exhausted_message,
    record_manifest,
    record_stage_attempt,
)
from tools.runner_stages import append_event, event
from tools.runner_types import (
    Q1_MAX_ATTEMPTS,
    RunnerBlocked,
    RunnerRequest,
    RunStatus,
    StageEventContext,
    StageEventOutcome,
    StageExecution,
    StageResult,
    StageRunContext,
    retry_q1_repair,
    safe_q1_feedback,
)


@dataclass(frozen=True, slots=True)
class AttemptRuntime:
    wall_clock: Callable[[RunnerRequest], datetime]
    monotonic_clock: Callable[[RunnerRequest], int]
    sleep: Callable[[float], None]


@dataclass(frozen=True, slots=True)
class AttemptContext:
    stage_context: StageRunContext
    state: JSONMap
    log_path: Path
    batch_id: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    result: StageResult
    request: RunnerRequest
    retryable: bool


def execute_attempts(
    context: AttemptContext, runtime: AttemptRuntime
) -> AttemptOutcome:
    if context.stage_context.stage == "content-assembler":
        return _execute_q1_repairs(context, runtime)
    return _execute_transient_retry(context, runtime)


def _execute_transient_retry(
    context: AttemptContext, runtime: AttemptRuntime
) -> AttemptOutcome:
    first_attempt = next_stage_attempt(
        context.state,
        context.stage_context.request.root,
        context.stage_context.run_id,
        context.stage_context.stage,
    )
    first = _execute_attempt(context, runtime, first_attempt)
    if not first.retryable:
        return first
    runtime.sleep(1.0)
    return _execute_attempt(context, runtime, first_attempt + 1)


def _execute_q1_repairs(
    context: AttemptContext, runtime: AttemptRuntime
) -> AttemptOutcome:
    attempts_used = q1_attempts_used(
        context.state, context.stage_context.request.root, context.stage_context.run_id
    )
    if attempts_used >= Q1_MAX_ATTEMPTS:
        return AttemptOutcome(
            StageResult(
                RunStatus.FAILED,
                StageExecution.ATTEMPTED,
                q1_retry_exhausted_message(),
                details={
                    "retryable": False,
                    "next_action": q1_retry_exhausted_message(),
                    "retry_stage": "content-assembler",
                },
                error_type="q1_retry_exhausted",
            ),
            context.stage_context.request,
            False,
        )
    attempt = next_stage_attempt(
        context.state,
        context.stage_context.request.root,
        context.stage_context.run_id,
        context.stage_context.stage,
    ) - 1
    preflight_codes = active_q1_codes(context.stage_context.request.root)

    def repair(feedback: str | None) -> tuple[StageResult, int, RunnerRequest]:
        nonlocal attempt
        if read_cancellation(
            context.stage_context.request.root, context.stage_context.run_id
        ) is not None:
            return (
                StageResult(
                    RunStatus.CANCELLED,
                    StageExecution.NOT_CALLED,
                    "취소됨",
                ),
                attempt,
                context.stage_context.request,
            )
        attempt += 1
        stage_context = replace(
            context.stage_context,
            q1_feedback=feedback,
            q1_preflight_codes=preflight_codes,
            stage_attempt=attempt,
        )
        current_context = replace(context, stage_context=stage_context)
        outcome = _execute_attempt(current_context, runtime, attempt)
        return outcome.result, attempt, outcome.request

    result, request, _ = retry_q1_repair(repair, attempts_used, runtime.sleep)
    return AttemptOutcome(result, request, False)


def _execute_attempt(
    context: AttemptContext, runtime: AttemptRuntime, attempt: int
) -> AttemptOutcome:
    request = context.stage_context.request
    started_at = runtime.wall_clock(request)
    started_ns = runtime.monotonic_clock(request)
    active_request = request
    retryable = False
    stage_context = replace(context.stage_context, stage_attempt=attempt)
    record_stage_attempt(context.state, stage_context.stage, attempt)
    try:
        result = stage_action(stage_context)
        active_request = _project_result(context, result)
    except ArticleQualityFailure as error:
        result = StageResult(
            RunStatus.FAILED,
            StageExecution.ATTEMPTED,
            str(error),
            details=error.event_details,
            error_type=StageFailureType.QUALITY_FAILED.value,
        )
    except RunnerBlocked as error:
        result = StageResult(RunStatus.BLOCKED, StageExecution.ATTEMPTED, str(error))
    except StageExecutionError as error:
        retryable = error.retryable and stage_context.stage not in {
            "notion-rider",
            "naver-rider",
        }
        error_type = error.error_type
        if (
            stage_context.stage == "content-assembler"
            and error_type == StageFailureType.CONTRACT_FAILED.value
            and error.process_attempts == 0
        ):
            error_type = Q1FailureCode.CONTRACT_FAILURE.value
        result = StageResult(
            RunStatus.FAILED,
            StageExecution.ATTEMPTED,
            str(error),
            details={
                "retryable": retryable,
                "next_action": error.next_action or "단계 오류를 확인하세요.",
                "retry_stage": stage_context.stage,
                "process_attempts": error.process_attempts,
            },
            error_type=error_type,
        )
    except ContractError as error:
        result = StageResult(
            RunStatus.FAILED,
            StageExecution.ATTEMPTED,
            str(error),
            error_type=(
                Q1FailureCode.CONTRACT_FAILURE.value
                if context.stage_context.stage == "content-assembler"
                else StageFailureType.CONTRACT_FAILED.value
            ),
        )
    except (OSError, TimeoutError) as error:
        result = StageResult(
            RunStatus.FAILED,
            StageExecution.ATTEMPTED,
            f"transient local error: {error}",
            details={
                "retryable": stage_context.stage not in {"notion-rider", "naver-rider"},
                "next_action": "로컬 저장 공간과 파일 접근 상태를 확인한 뒤 다시 시도하세요.",
                "retry_stage": stage_context.stage,
            },
            error_type=StageFailureType.TEMPORARY_IO.value,
        )
        retryable = stage_context.stage not in {"notion-rider", "naver-rider"}
    if (
        context.stage_context.stage == "content-assembler"
        and result.status is RunStatus.FAILED
    ):
        result = replace(result, message=safe_q1_feedback(result.message))
    ended_ns = runtime.monotonic_clock(request)
    ended_at = runtime.wall_clock(request)
    duration_ms = (ended_ns - started_ns) / 1_000_000
    append_event(
        context.log_path,
        event(
            StageEventContext(
                active_request,
                context.stage_context.run_id,
                context.batch_id,
                context.stage_context.stage,
                started_at.isoformat(),
                ended_at.isoformat(),
                attempt,
                duration_ms,
                context.depends_on,
                (
                    active_request.selection_context.batch_slot
                    if active_request.selection_context is not None
                    else None
                ),
            ),
            StageEventOutcome(
                result.status,
                result.execution,
                result.message or result.status.value,
                result.details,
                result.error_type,
            ),
        ),
    )
    return AttemptOutcome(result, active_request, retryable)


def _project_result(context: AttemptContext, result: StageResult) -> RunnerRequest:
    request = context.stage_context.request
    active_request = request
    if result.resolved_keyword is not None:
        if (
            not request.auto_topic
            and request.keyword is not None
            and result.resolved_keyword != request.keyword
        ):
            raise ContractError("topic-selector cannot replace a user-defined keyword")
        active_request = replace(request, keyword=result.resolved_keyword)
        context.state["keyword"] = result.resolved_keyword
        context.state["topic_id"] = f"TOPIC-{result.resolved_keyword}"
    if result.details is not None:
        context.state.update(result.details)
        selection = active_request.selection_context
        snapshot_path = result.details.get("selection_snapshot_path")
        snapshot_digest = result.details.get("selection_snapshot_sha256")
        if (
            context.stage_context.stage == "topic-selector"
            and selection is not None
            and isinstance(snapshot_path, str)
            and isinstance(snapshot_digest, str)
        ):
            updated_selection = replace(
                selection,
                snapshot_path=snapshot_path,
                snapshot_sha256=snapshot_digest,
            )
            active_request = replace(
                active_request, selection_context=updated_selection
            )
            context.state["selection_context"] = updated_selection.as_json()
    record_manifest(context.state, active_request, context.stage_context.run_id)
    return active_request
