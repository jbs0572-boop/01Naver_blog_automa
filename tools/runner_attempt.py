from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.runner_actions import stage_action
from tools.runner_records import record_manifest
from tools.runner_stages import append_event, event
from tools.runner_types import (
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
    first = _execute_attempt(context, runtime, 1)
    if not first.retryable:
        return first
    runtime.sleep(0.05)
    return _execute_attempt(context, runtime, 2)


def _execute_q1_repairs(
    context: AttemptContext, runtime: AttemptRuntime
) -> AttemptOutcome:
    attempt = 0

    def repair(feedback: str | None) -> tuple[StageResult, int, RunnerRequest]:
        nonlocal attempt
        attempt += 1
        stage_context = replace(context.stage_context, q1_feedback=feedback)
        current_context = replace(context, stage_context=stage_context)
        outcome = _execute_attempt(current_context, runtime, attempt)
        return outcome.result, attempt, outcome.request

    result, request, _ = retry_q1_repair(repair)
    return AttemptOutcome(result, request, False)


def _execute_attempt(
    context: AttemptContext, runtime: AttemptRuntime, attempt: int
) -> AttemptOutcome:
    request = context.stage_context.request
    started_at = runtime.wall_clock(request)
    started_ns = runtime.monotonic_clock(request)
    active_request = request
    retryable = False
    try:
        result = stage_action(context.stage_context)
        active_request = _project_result(context, result)
    except RunnerBlocked as error:
        result = StageResult(RunStatus.BLOCKED, StageExecution.ATTEMPTED, str(error))
    except ContractError as error:
        result = StageResult(RunStatus.FAILED, StageExecution.ATTEMPTED, str(error))
    except (OSError, TimeoutError) as error:
        result = StageResult(
            RunStatus.FAILED,
            StageExecution.ATTEMPTED,
            f"transient local error: {error}",
        )
        retryable = True
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
            ),
            StageEventOutcome(
                result.status,
                result.execution,
                result.message or result.status.value,
                result.details,
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
    record_manifest(context.state, active_request, context.stage_context.run_id)
    return active_request
