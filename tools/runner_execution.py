from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from tools.codex_stage_executor import CodexStageExecutor
from tools.contract_types import ContractError, JSONMap
from tools.runner_job import (
    codex_project_is_configured,
    find_duplicate_job,
    new_run_id,
    validate_job_request,
)
from tools.runner_job import job_key as _job_key
from tools.runner_lock import acquire_lock
from tools.runner_loop import execute_run
from tools.runner_records import blocked_result
from tools.runner_stages import (
    append_event,
    now,
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
    ConfirmationInput,
    JobName,
    RunExecutionContext,
    RunnerBlocked,
    RunnerRequest,
    RunnerResult,
    RunStatus,
    StageExecution,
)


def job_key(request: RunnerRequest) -> str:
    return _job_key(request)


def _run(request: RunnerRequest, allow_existing: bool = False) -> RunnerResult:
    job = validate_job_request(request)
    run_id = request.run_id or new_run_id(request)
    state_path, log_path, lock_path = state_paths(
        request.root, run_id, request.state_dir
    )
    if job is JobName.NAVER_PUBLISH and state_path.is_file():
        state = read_state(state_path)
        if state.get("job") != JobName.DAILY_GENERATE.value:
            raise ContractError("naver-publish requires a daily-generate run")
        return resume_job(
            replace(
                request,
                job=JobName.DAILY_GENERATE.value,
                resume=True,
            )
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
    duplicate = find_duplicate_job(request, job.value, request.state_dir)
    if duplicate is not None and duplicate[0] != run_id:
        return result_from_state(duplicate[1], duplicate[2], duplicate[3])
    if request.executor is None and codex_project_is_configured(request.root):
        request = replace(request, executor=CodexStageExecutor())
    metadata: JSONMap = {
        "pid": os.getpid(),
        "run_id": run_id,
        "job": job.value,
        "created_at": now(request).isoformat(),
    }
    with acquire_lock(lock_path, metadata):
        return execute_run(RunExecutionContext(
            request, job, run_id, state_path, log_path, input_hash, allow_existing
        ))


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

def resume_job(request: RunnerRequest) -> RunnerResult:
    if request.run_id is None:
        raise ContractError("resume requires run_id")
    state_path, _, _ = state_paths(request.root, request.run_id, request.state_dir)
    state = read_state(state_path)
    recovered = request_from_state(state, request.root, request.state_dir)
    return _run(
        replace(
            recovered,
            resume=True,
            executor=request.executor,
            notion_adapter=request.notion_adapter,
            naver_adapter=request.naver_adapter,
        ),
        allow_existing=True,
    )


def confirm_job(confirmation_input: ConfirmationInput) -> RunnerResult:
    root = confirmation_input.root
    run_id = confirmation_input.run_id
    action = confirmation_input.action
    state_dir = confirmation_input.state_dir
    state_path, log_path, _ = state_paths(root, run_id, state_dir)
    state = read_state(state_path)
    if state.get("status") != RunStatus.AWAITING_USER_CONFIRMATION.value:
        raise ContractError("run is not awaiting user confirmation")
    if action != "naver-draft-save":
        raise ContractError("unsupported confirmation action")
    confirmation: JSONMap = {
        "run_id": run_id,
        "action": action,
        "target_blog_id": state.get("target_blog_id"),
        "title": state.get("naver_title"),
        "artifact_digest": state.get("artifact_digest"),
        "requested_at": state.get("confirmation_requested_at"),
        "confirmed_at": datetime.now().astimezone().isoformat(),
        "actor": confirmation_input.actor,
    }
    if not all(
        isinstance(confirmation.get(key), str)
        for key in ("target_blog_id", "title", "artifact_digest", "requested_at")
    ):
        raise ContractError("confirmation preview is incomplete")
    state["confirmation"] = confirmation
    state["status"] = RunStatus.RUNNING.value
    state["message"] = "operator confirmation received; resuming Naver draft save"
    stages = state.get("stages")
    executions = state.get("stage_execution")
    if isinstance(stages, dict):
        stages["naver-rider"] = RunStatus.PENDING.value
    if isinstance(executions, dict):
        executions["naver-rider"] = StageExecution.NOT_CALLED.value
    atomic_write_json(state_path, state)
    append_event(
        log_path,
        {
            "event_type": "confirmation",
            "pipeline_version": "workflow-optimized-v1",
            "run_id": run_id,
            "action": action,
            "target_blog_id": confirmation["target_blog_id"],
            "title": confirmation["title"],
            "artifact_digest": confirmation["artifact_digest"],
            "requested_at": confirmation["requested_at"],
            "confirmed_at": confirmation["confirmed_at"],
            "actor": confirmation_input.actor,
        },
    )
    return resume_job(
        RunnerRequest(
            root=root,
            job="",
            run_id=run_id,
            state_dir=state_dir,
            executor=confirmation_input.executor,
            notion_adapter=confirmation_input.notion_adapter,
            naver_adapter=confirmation_input.naver_adapter,
        )
    )
