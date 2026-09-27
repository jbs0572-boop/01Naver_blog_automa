from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from tools.codex_stage_executor import CodexStageExecutor
from tools.contract_types import ContractError, JSONMap
from tools.notion_keychain import NotionCredentialsUnavailable, load_notion_api_token
from tools.run_cancellation import read_cancellation
from tools.runner_confirmation import apply_confirmation
from tools.runner_job import (
    codex_project_is_configured,
    find_duplicate_job,
    new_run_id,
    validate_job_request,
)
from tools.runner_job import job_key as _job_key
from tools.runner_lock import acquire_lock
from tools.runner_loop import execute_run
from tools.runner_naver_action import invalidate_naver_preparation as _invalidate_naver
from tools.runner_notion_action import configured_notion_target
from tools.runner_records import blocked_result
from tools.runner_secure_fs import secure_entrypoint
from tools.runner_stages import now
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
)
from tools.topic_feedback_pinning import pin_daily_request


def pin_topic_feedback_context(request: RunnerRequest, job: JobName) -> RunnerRequest:
    if job is not JobName.DAILY_GENERATE:
        return request
    return pin_daily_request(request)


def job_key(request: RunnerRequest) -> str:
    return _job_key(request)


def invalidate_naver_preparation(root: Path, run_id: str) -> None:
    _invalidate_naver(root, run_id)


def _run(
    request: RunnerRequest,
    allow_existing: bool = False,
    *,
    lease_held: bool = False,
) -> RunnerResult:
    if request.dry_run:
        request = replace(request, notion_adapter=None, naver_adapter=None)
    job = validate_job_request(request)
    request = _pin_notion_target(request, job)
    request = pin_topic_feedback_context(request, job)
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
        sensitive_values: tuple[str, ...] = ()
        if (
            job is JobName.DAILY_GENERATE
            and not request.dry_run
            and request.notion_adapter is not None
        ):
            try:
                sensitive_values = (str(load_notion_api_token()),)
            except NotionCredentialsUnavailable:
                raise ContractError("notion_credentials_unavailable") from None
        request = replace(
            request,
            executor=CodexStageExecutor(sensitive_values=sensitive_values),
        )
    context = RunExecutionContext(
        request, job, run_id, state_path, log_path, input_hash, allow_existing
    )
    if lease_held:
        return execute_run(context)
    metadata: JSONMap = {
        "pid": os.getpid(),
        "run_id": run_id,
        "job": job.value,
        "created_at": now(request).isoformat(),
    }
    with acquire_lock(lock_path, metadata):
        return execute_run(context)


def _pin_notion_target(request: RunnerRequest, job: JobName) -> RunnerRequest:
    if (
        job is not JobName.DAILY_GENERATE
        or request.dry_run
        or request.notion_adapter is None
    ):
        return request
    configured_target = configured_notion_target(request.root)
    if request.notion_target_id is None:
        if request.resume:
            raise ContractError("live resume requires a pinned Notion target")
        return replace(request, notion_target_id=configured_target)
    if request.notion_target_id != configured_target:
        raise ContractError("pinned Notion target changed in notion-config.md")
    return request


@secure_entrypoint
def run_job(request: RunnerRequest) -> RunnerResult:
    try:
        return _run(request)
    except RunnerBlocked as error:
        run_id = request.run_id or stable_run_id(request)
        state_path, log_path, _ = state_paths(request.root, run_id, request.state_dir)
        return blocked_result(run_id, state_path, log_path, str(error))


@secure_entrypoint
def recover_job(request: RunnerRequest) -> RunnerResult:
    if request.run_id is None:
        raise ContractError("recover requires run_id")
    state_path, log_path, lock_path = state_paths(
        request.root, request.run_id, request.state_dir
    )
    metadata: JSONMap = {
        "pid": os.getpid(),
        "run_id": request.run_id,
        "job": "recover",
        "created_at": now(request).isoformat(),
    }
    try:
        with acquire_lock(lock_path, metadata):
            state = read_state(state_path)
            if state.get("naver_save_outcome_uncertain") is True:
                raise ContractError(
                    "Naver save outcome is uncertain; reconcile the existing draft before recovery"
                )
            if state.get("status") in {RunStatus.DRAFT_SAVED.value, RunStatus.CANCELLED.value} or read_cancellation(request.root, request.run_id) is not None:
                return result_from_state(state, state_path, log_path)
            recovered = request_from_state(state, request.root, request.state_dir)
            current_hash = input_fingerprint(recovered)
            if state.get("input_hash") != current_hash:
                state["status"] = RunStatus.BLOCKED.value
                state["message"] = "input hash changed; recovery refused"
                state["updated_at"] = now(request).isoformat()
                atomic_write_json(state_path, state)
                return blocked_result(
                    request.run_id,
                    state_path,
                    log_path,
                    "input hash changed; recovery refused",
                )
            effective_dry_run = recovered.dry_run or request.dry_run
            return _run(
                replace(
                    recovered,
                    dry_run=effective_dry_run,
                    executor=request.executor,
                    notion_adapter=(
                        None if effective_dry_run else request.notion_adapter
                    ),
                    naver_adapter=None if effective_dry_run else request.naver_adapter,
                ),
                allow_existing=True,
                lease_held=True,
            )
    except RunnerBlocked as error:
        return blocked_result(request.run_id, state_path, log_path, str(error))


@secure_entrypoint
def get_status(root: Path, run_id: str, state_dir: Path | None = None) -> JSONMap:
    state_path, _, _ = state_paths(root, run_id, state_dir)
    return read_state(state_path)


def is_transient_error(error: BaseException | str) -> bool:
    if isinstance(error, str):
        return error.lower() in {"timeout", "temporary_unavailable", "io_error"}
    return isinstance(error, (OSError, TimeoutError))


def _resume_job(request: RunnerRequest, *, lease_held: bool) -> RunnerResult:
    if request.run_id is None:
        raise ContractError("resume requires run_id")
    state_path, _, _ = state_paths(request.root, request.run_id, request.state_dir)
    state = read_state(state_path)
    if state.get("naver_save_outcome_uncertain") is True:
        raise ContractError(
            "Naver save outcome is uncertain; reconcile the existing draft before resume"
        )
    if state.get("status") == RunStatus.CANCELLED.value or read_cancellation(request.root, request.run_id) is not None:
        return result_from_state(state, state_path, state_path.with_suffix(".jsonl"))
    recovered = request_from_state(state, request.root, request.state_dir)
    effective_dry_run = recovered.dry_run or request.dry_run
    return _run(
        replace(
            recovered,
            resume=True,
            dry_run=effective_dry_run,
            executor=request.executor,
            notion_adapter=(None if effective_dry_run else request.notion_adapter),
            naver_adapter=None if effective_dry_run else request.naver_adapter,
        ),
        allow_existing=True,
        lease_held=lease_held,
    )


@secure_entrypoint
def resume_job(request: RunnerRequest) -> RunnerResult:
    return _resume_job(request, lease_held=False)


@secure_entrypoint
def confirm_job(confirmation_input: ConfirmationInput) -> RunnerResult:
    if read_cancellation(confirmation_input.root, confirmation_input.run_id) is not None:
        state_path, log_path, _ = state_paths(confirmation_input.root, confirmation_input.run_id)
        return result_from_state(read_state(state_path), state_path, log_path)
    return apply_confirmation(
        confirmation_input,
        lambda request: _resume_job(request, lease_held=True),
    )
