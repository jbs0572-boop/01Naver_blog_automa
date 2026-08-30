from __future__ import annotations

from dataclasses import replace

from tools.contract_types import ContractError, JSONMap
from tools.runner_actions import stage_action
from tools.runner_job import job_key
from tools.runner_records import (
    blocked_result,
    initial_state,
    record_manifest,
    set_stage,
    set_stage_execution,
    state_output_hash,
)
from tools.runner_stages import (
    append_event,
    event,
    now,
    with_retry,
)
from tools.runner_state import (
    atomic_write_json,
    input_fingerprint,
    read_state,
    result_from_state,
)
from tools.runner_types import (
    STAGE_ORDER,
    JobName,
    RunExecutionContext,
    RunnerBlocked,
    RunnerRequest,
    RunnerResult,
    RunStatus,
    StageEventContext,
    StageEventOutcome,
    StageExecution,
    StageResult,
    StageRunContext,
    retry_q1_repair,
)


def execute_run(context: RunExecutionContext) -> RunnerResult:
    request = context.request
    job = context.job
    run_id = context.run_id
    state_path = context.state_path
    log_path = context.log_path
    input_hash = context.input_hash
    allow_existing = context.allow_existing
    timestamp = now(request).isoformat()
    active_request = request
    if state_path.is_file():
        state = read_state(state_path)
        if state.get("input_hash") != input_hash:
            return blocked_result(
                run_id, state_path, log_path,
                "input hash changed; recovery requires a fresh run",
            )
        existing_status = state.get("status")
        if not allow_existing or existing_status == RunStatus.PASSED.value:
            return result_from_state(state, state_path, log_path)
        _reset_incomplete_stages(state)
    else:
        state = initial_state(request, run_id, input_hash, timestamp)
    _ = state.setdefault("job_key", job_key(request))
    state.update(
        status=RunStatus.RUNNING.value,
        updated_at=timestamp,
        message="run is executing locally",
    )
    atomic_write_json(state_path, state)
    batch_id = "BATCH-" + now(request).date().isoformat()
    for index, stage in enumerate(STAGE_ORDER):
        stages = state.get("stages")
        if isinstance(stages, dict) and stages.get(stage) in {
            RunStatus.PASSED.value, RunStatus.VALIDATED.value,
        }:
            continue
        set_stage(state, stage, RunStatus.RUNNING)
        set_stage_execution(state, stage, StageExecution.NOT_CALLED)
        state["updated_at"] = now(request).isoformat()
        atomic_write_json(state_path, state)
        stage_context = StageRunContext(active_request, job, stage, run_id, timestamp)
        if stage == "content-assembler":
            stage_result, active_request, stage_attempts = retry_q1_repair(
                lambda feedback, stage_context=stage_context, state=state: _run_stage(
                    replace(stage_context, q1_feedback=feedback), state
                )
            )
            attempt = 1
        else:
            stage_result, attempt, active_request = _run_stage(stage_context, state)
            stage_attempts = (stage_result,)
        if stage_result.run_status is not None and not (
            (
                stage == "notion-rider"
                and stage_result.run_status is RunStatus.READY_FOR_NAVER
            )
            or (
                stage == "naver-rider"
                and stage_result.run_status
                in {RunStatus.AWAITING_USER_CONFIRMATION, RunStatus.DRAFT_SAVED}
            )
        ):
            stage_result = replace(
                stage_result,
                status=RunStatus.FAILED,
                message=(
                    f"{stage} cannot set run_status; only notion-rider and "
                    "naver-rider may set a terminal run status"
                ),
                run_status=None,
            )
        result = stage_result.status
        message = stage_result.message or (
            "stage passed" if result is RunStatus.PASSED else result.value
        )
        for current_attempt, attempt_result in enumerate(stage_attempts, attempt):
            attempt_message = attempt_result.message or attempt_result.status.value
            append_event(
                log_path,
                event(
                    StageEventContext(
                        active_request, run_id, batch_id, stage, timestamp,
                        now(request).isoformat(), current_attempt,
                    ),
                    StageEventOutcome(
                        attempt_result.status, attempt_result.execution, attempt_message
                    ),
                ),
            )
        set_stage(state, stage, result)
        set_stage_execution(state, stage, stage_result.execution)
        if result in {RunStatus.PASSED, RunStatus.VALIDATED}:
            state["input_hash"] = input_fingerprint(active_request)
        if stage_result.run_status is not None:
            state.update(
                status=stage_result.run_status.value,
                message=message,
                updated_at=now(active_request).isoformat(),
            )
            atomic_write_json(state_path, state)
            if stage_result.run_status is RunStatus.AWAITING_USER_CONFIRMATION:
                return result_from_state(state, state_path, log_path)
        if job is JobName.WEEKLY_IMPROVE and stage == "researcher":
            state["message"] = message
        state["updated_at"] = now(request).isoformat()
        if result in {RunStatus.FAILED, RunStatus.BLOCKED}:
            for downstream in STAGE_ORDER[index + 1 :]:
                set_stage(state, downstream, RunStatus.SKIPPED)
            state.update(
                status=result.value,
                message=message,
                output_hash=state_output_hash(state, request, run_id),
            )
            atomic_write_json(state_path, state)
            return result_from_state(state, state_path, log_path)
        atomic_write_json(state_path, state)
    return _finish_run(replace(context, request=active_request), state)


def _reset_incomplete_stages(state: JSONMap) -> None:
    stages = state.get("stages")
    if not isinstance(stages, dict):
        return
    executions_value = state.get("stage_execution")
    executions: JSONMap
    if isinstance(executions_value, dict):
        executions = executions_value
    else:
        executions = {
            stage: StageExecution.NOT_CALLED.value for stage in STAGE_ORDER
        }
    state["stage_execution"] = executions
    for stage in STAGE_ORDER:
        if stages.get(stage) not in {RunStatus.PASSED.value, RunStatus.VALIDATED.value}:
            stages[stage] = RunStatus.PENDING.value
            executions[stage] = StageExecution.NOT_CALLED.value


def _run_stage(
    context: StageRunContext,
    state: JSONMap,
) -> tuple[StageResult, int, RunnerRequest]:
    request = context.request
    try:
        if context.stage == "content-assembler":
            result, attempt = stage_action(context), 1
        else:
            result, attempt = with_retry(lambda: stage_action(context))
        active = request
        if result.resolved_keyword is not None:
            if (
                not request.auto_topic
                and request.keyword is not None
                and result.resolved_keyword != request.keyword
            ):
                raise ContractError(
                    "topic-selector cannot replace a user-defined keyword"
                )
            active = replace(request, keyword=result.resolved_keyword)
            state["keyword"] = result.resolved_keyword
            state["topic_id"] = f"TOPIC-{result.resolved_keyword}"
        if result.details is not None:
            state.update(result.details)
        record_manifest(state, active, context.run_id)
        return result, attempt, active
    except RunnerBlocked as error:
        return StageResult(
            RunStatus.BLOCKED, StageExecution.ATTEMPTED, str(error)
        ), 1, request
    except ContractError as error:
        return StageResult(
            RunStatus.FAILED, StageExecution.ATTEMPTED, str(error)
        ), 1, request
    except (OSError, TimeoutError) as error:
        return StageResult(
            RunStatus.FAILED,
            StageExecution.ATTEMPTED,
            f"transient local error: {error}",
        ), 2, request


def _finish_run(
    context: RunExecutionContext,
    state: JSONMap,
) -> RunnerResult:
    request = context.request
    if state.get("status") not in {
        RunStatus.DRAFT_SAVED.value, RunStatus.READY_FOR_NAVER.value,
    }:
        state["status"] = (
            RunStatus.PASSED.value
            if context.job is JobName.WEEKLY_IMPROVE
            else RunStatus.LOCAL_ONLY.value
        )
    if (
        context.job is JobName.DAILY_GENERATE
        and state["status"] == RunStatus.LOCAL_ONLY.value
    ):
        stages = state.get("stages")
        pending = isinstance(stages, dict) and any(
            stages.get(stage) == RunStatus.SKIPPED.value
            for stage in ("notion-rider", "naver-rider")
        )
        state["message"] = (
            "content workflow completed; external storage is pending"
            if pending
            else "local validation completed; producers and external integrations were not called"
        )
    state.update(
        output_hash=state_output_hash(state, request, context.run_id),
        updated_at=now(request).isoformat(),
    )
    atomic_write_json(context.state_path, state)
    return result_from_state(state, context.state_path, context.log_path)


__all__ = ["execute_run"]
