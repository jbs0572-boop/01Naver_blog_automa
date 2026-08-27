from __future__ import annotations

import os
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.runner_lock import acquire_lock
from tools.runner_stages import (
    append_event,
    blocked_result,
    event,
    initial_state,
    now,
    record_manifest,
    set_stage,
    stage_action,
    state_output_hash,
    validated_job,
    with_retry,
)
from tools.runner_state import (
    atomic_write_json,
    input_fingerprint,
    read_state,
    request_from_state,
    result_from_state,
    stable_run_id,
    state_paths,
)
from tools.runner_types import (
    STAGE_ORDER,
    JobName,
    RunnerBlocked,
    RunnerRequest,
    RunnerResult,
    RunStatus,
)


def _execute(
    request: RunnerRequest,
    job: JobName,
    run_id: str,
    state_path: Path,
    log_path: Path,
    input_hash: str,
    allow_existing: bool,
) -> RunnerResult:
    timestamp = now(request).isoformat()
    if state_path.is_file():
        state = read_state(state_path)
        if state.get("input_hash") != input_hash:
            return blocked_result(
                run_id,
                state_path,
                log_path,
                "input hash changed; recovery requires a fresh run",
            )
        existing_status = state.get("status")
        if not allow_existing or existing_status == RunStatus.PASSED.value:
            return result_from_state(state, state_path, log_path)
        stages = state.get("stages")
        if isinstance(stages, dict):
            for stage in STAGE_ORDER:
                if stages.get(stage) != RunStatus.PASSED.value:
                    stages[stage] = RunStatus.PENDING.value
    else:
        state = initial_state(request, run_id, input_hash, timestamp)
    state["status"] = RunStatus.RUNNING.value
    state["updated_at"] = timestamp
    state["message"] = "run is executing locally"
    atomic_write_json(state_path, state)
    batch_id = "BATCH-" + now(request).date().isoformat()
    for index, stage in enumerate(STAGE_ORDER):
        stages = state.get("stages")
        if isinstance(stages, dict) and stages.get(stage) == RunStatus.PASSED.value:
            continue
        set_stage(state, stage, RunStatus.RUNNING)
        state["updated_at"] = now(request).isoformat()
        atomic_write_json(state_path, state)
        try:
            (stage_result, attempt) = with_retry(
                lambda current_stage=stage: stage_action(
                    request, job, current_stage, run_id, timestamp
                )
            )
            result, message = stage_result
            record_manifest(state, request, run_id)
        except RunnerBlocked as error:
            result, attempt = RunStatus.BLOCKED, 1
            message = str(error)
        except ContractError as error:
            result, attempt = RunStatus.FAILED, 1
            message = str(error)
        except (OSError, TimeoutError) as error:
            result, attempt = RunStatus.FAILED, 2
            message = f"transient local error: {error}"
        else:
            message = message or (
                "stage passed" if result is RunStatus.PASSED else result.value
            )
        append_event(
            log_path,
            event(
                request,
                run_id,
                batch_id,
                stage,
                result,
                timestamp,
                now(request).isoformat(),
                attempt,
                message,
            ),
        )
        set_stage(state, stage, result)
        if job is JobName.WEEKLY_IMPROVE and stage == "researcher":
            state["message"] = message
        state["updated_at"] = now(request).isoformat()
        if result in {RunStatus.FAILED, RunStatus.BLOCKED}:
            for downstream in STAGE_ORDER[index + 1 :]:
                set_stage(state, downstream, RunStatus.SKIPPED)
            state["status"] = result.value
            state["message"] = message
            state["output_hash"] = state_output_hash(state, request, run_id)
            atomic_write_json(state_path, state)
            return result_from_state(state, state_path, log_path)
        atomic_write_json(state_path, state)
    state["status"] = RunStatus.PASSED.value
    if job is not JobName.WEEKLY_IMPROVE:
        state["message"] = (
            "local workflow completed; external integrations were not called"
        )
    state["output_hash"] = state_output_hash(state, request, run_id)
    state["updated_at"] = now(request).isoformat()
    atomic_write_json(state_path, state)
    return result_from_state(state, state_path, log_path)


def _run(request: RunnerRequest, allow_existing: bool = False) -> RunnerResult:
    job = validated_job(request)
    run_id = request.run_id or stable_run_id(request)
    state_path, log_path, lock_path = state_paths(
        request.root, run_id, request.state_dir
    )
    input_hash = input_fingerprint(request)
    if state_path.is_file() and not allow_existing:
        state = read_state(state_path)
        if state.get("input_hash") != input_hash:
            return blocked_result(
                run_id,
                state_path,
                log_path,
                "input hash changed; recovery requires a fresh run",
            )
        return result_from_state(state, state_path, log_path)
    metadata: JSONMap = {
        "pid": os.getpid(),
        "run_id": run_id,
        "job": job.value,
        "created_at": now(request).isoformat(),
    }
    with acquire_lock(lock_path, metadata):
        return _execute(
            request, job, run_id, state_path, log_path, input_hash, allow_existing
        )


def run_job(request: RunnerRequest) -> RunnerResult:
    try:
        return _run(request)
    except RunnerBlocked as error:
        run_id = request.run_id or stable_run_id(request)
        state_path, log_path, _ = state_paths(request.root, run_id, request.state_dir)
        return blocked_result(run_id, state_path, log_path, str(error))


def recover_job(request: RunnerRequest) -> RunnerResult:
    if request.run_id is None:
        raise ContractError("recover requires run_id")
    state_path, log_path, _ = state_paths(
        request.root, request.run_id, request.state_dir
    )
    state = read_state(state_path)
    recovered = request_from_state(state, request.root, request.state_dir)
    current_hash = input_fingerprint(recovered)
    if state.get("input_hash") != current_hash:
        state["status"] = RunStatus.BLOCKED.value
        state["message"] = "input hash changed; recovery refused"
        state["updated_at"] = now(request).isoformat()
        atomic_write_json(state_path, state)
        return blocked_result(
            request.run_id, state_path, log_path, "input hash changed; recovery refused"
        )
    return _run(recovered, allow_existing=True)


def get_status(root: Path, run_id: str, state_dir: Path | None = None) -> JSONMap:
    state_path, _, _ = state_paths(root, run_id, state_dir)
    return read_state(state_path)


def is_transient_error(error: BaseException | str) -> bool:
    if isinstance(error, str):
        return error.lower() in {"timeout", "temporary_unavailable", "io_error"}
    return isinstance(error, (OSError, TimeoutError))
