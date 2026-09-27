from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tools import runner_stages
from tools.contract_types import JSONMap
from tools.run_cancellation import read_cancellation
from tools.runner_attempt import AttemptContext, AttemptRuntime, execute_attempts
from tools.runner_job import job_key
from tools.runner_records import (
    blocked_result,
    initial_state,
    set_stage,
    set_stage_execution,
    state_output_hash,
)
from tools.runner_stages import (
    monotonic_ns,
    now,
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
    RunnerResult,
    RunStatus,
    StageExecution,
    StageRunContext,
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
    cancellation = read_cancellation(request.root, run_id)
    if cancellation is not None:
        return _cancelled_run(context, state_path, log_path, cancellation.requested_at)
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
    if state.get("job_key") is None:
        state["job_key"] = job_key(request)
    state.update(
        status=RunStatus.RUNNING.value,
        updated_at=timestamp,
        message="run is executing locally",
    )
    atomic_write_json(state_path, state)
    selection_context = active_request.selection_context
    batch_id = (
        selection_context.batch_id
        if selection_context is not None and selection_context.batch_id is not None
        else f"BATCH-{run_id}"
    )
    for index, stage in enumerate(STAGE_ORDER):
        stages = state.get("stages")
        if isinstance(stages, dict) and stages.get(stage) in {
            RunStatus.PASSED.value, RunStatus.VALIDATED.value,
        }:
            continue
        if read_cancellation(request.root, run_id) is not None:
            return _cancelled_run(context, state_path, log_path, now(request).isoformat(), state)
        set_stage(state, stage, RunStatus.RUNNING)
        set_stage_execution(state, stage, StageExecution.NOT_CALLED)
        if stage == "naver-rider" and active_request.confirmed:
            state["naver_save_outcome_uncertain"] = True
        state["updated_at"] = now(request).isoformat()
        atomic_write_json(state_path, state)
        stage_context = StageRunContext(active_request, job, stage, run_id, timestamp)
        depends_on = () if index == 0 else (STAGE_ORDER[index - 1],)
        attempt_outcome = execute_attempts(
            AttemptContext(stage_context, state, log_path, batch_id, depends_on),
            AttemptRuntime(now, monotonic_ns, runner_stages.sleep),
        )
        stage_result = attempt_outcome.result
        active_request = attempt_outcome.request
        if stage_result.run_status is RunStatus.DRAFT_SAVED:
            _ = state.pop("naver_save_outcome_uncertain", None)
        if read_cancellation(request.root, run_id) is not None and stage_result.run_status is not RunStatus.DRAFT_SAVED:
            return _cancelled_run(context, state_path, log_path, now(request).isoformat(), state, stage)
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
            if stage_result.run_status in {
                RunStatus.READY_FOR_NAVER,
                RunStatus.AWAITING_USER_CONFIRMATION,
            }:
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


def _cancelled_run(
    context: RunExecutionContext,
    state_path: Path,
    log_path: Path,
    requested_at: str,
    state: JSONMap | None = None,
    current_stage: str | None = None,
) -> RunnerResult:
    if state is None:
        state = initial_state(context.request, context.run_id, context.input_hash, requested_at)
    stages = state.get("stages")
    if isinstance(stages, dict):
        if current_stage is not None and stages.get(current_stage) == RunStatus.RUNNING.value:
            stages[current_stage] = RunStatus.CANCELLED.value
        for stage in STAGE_ORDER:
            if stages.get(stage) in {RunStatus.PENDING.value, RunStatus.RUNNING.value}:
                stages[stage] = RunStatus.SKIPPED.value
    state.update(status=RunStatus.CANCELLED.value, message="cancelled", updated_at=requested_at)
    atomic_write_json(state_path, state)
    return result_from_state(state, state_path, log_path)


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
