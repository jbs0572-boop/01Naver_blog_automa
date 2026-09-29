from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime

from tools.contract_types import ContractError, JSONMap
from tools.runner_lock import acquire_lock
from tools.runner_stages import append_event
from tools.runner_state import (
    atomic_write_json,
    read_state,
    request_from_state,
    state_paths,
)
from tools.runner_types import (
    ConfirmationInput,
    RunnerRequest,
    RunnerResult,
    RunStatus,
    StageExecution,
)
from tools.topic_feedback_pinning import pin_daily_request


def apply_confirmation(
    confirmation_input: ConfirmationInput,
    resume: Callable[[RunnerRequest], RunnerResult],
) -> RunnerResult:
    _, _, lock_path = state_paths(
        confirmation_input.root,
        confirmation_input.run_id,
        confirmation_input.state_dir,
    )
    metadata: JSONMap = {
        "pid": os.getpid(),
        "run_id": confirmation_input.run_id,
        "job": "confirmation",
    }
    with acquire_lock(lock_path, metadata):
        request = _record_confirmation(confirmation_input)
        return resume(request)


def _record_confirmation(confirmation_input: ConfirmationInput) -> RunnerRequest:
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
    recovered = request_from_state(state, root, state_dir)
    _ = pin_daily_request(recovered)
    active_nonce = state.get("confirmation_nonce")
    if (
        not isinstance(active_nonce, str)
        or confirmation_input.confirmation_nonce != active_nonce
    ):
        raise ContractError("confirmation nonce is stale or invalid")
    automatic = confirmation_input.actor == "dashboard-auto-save"
    recorded_at = datetime.now().astimezone().isoformat()
    confirmation: JSONMap = {
        "run_id": run_id,
        "action": action,
        "target_blog_id": state.get("target_blog_id"),
        "title": state.get("naver_title"),
        "artifact_digest": state.get("artifact_digest"),
        "confirmation_request_digest": state.get("confirmation_request_digest"),
        "requested_at": state.get("confirmation_requested_at"),
        "confirmed_at": recorded_at,
        "actor": confirmation_input.actor,
    }
    required = (
        "target_blog_id",
        "title",
        "artifact_digest",
        "confirmation_request_digest",
        "requested_at",
    )
    if not all(isinstance(confirmation.get(key), str) for key in required):
        raise ContractError("confirmation preview is incomplete")
    if automatic:
        authorization = {
            "run_id": run_id,
            "action": action,
            "policy": "all_quality_gates_passed",
            "target_blog_id": confirmation["target_blog_id"],
            "title": confirmation["title"],
            "artifact_digest": confirmation["artifact_digest"],
            "authorization_digest": confirmation["confirmation_request_digest"],
            "requested_at": confirmation["requested_at"],
            "authorized_at": recorded_at,
            "actor": "dashboard-auto-save",
        }
        append_event(
            log_path,
            {
                "event_type": "workflow_authorization",
                "pipeline_version": "workflow-optimized-v1",
                **authorization,
            },
        )
        state["naver_save_authorization"] = authorization
        state["confirmation"] = None
    else:
        append_event(
            log_path,
            {
                "event_type": "confirmation",
                "pipeline_version": "workflow-optimized-v1",
                **confirmation,
            },
        )
        state["confirmation"] = confirmation
    if "confirmation_nonce" in state:
        del state["confirmation_nonce"]
    state["status"] = RunStatus.RUNNING.value
    state["message"] = (
        "quality gates passed; dashboard policy authorized Naver draft save"
        if automatic
        else "operator confirmation received; resuming Naver draft save"
    )
    stages = state.get("stages")
    executions = state.get("stage_execution")
    if isinstance(stages, dict):
        stages["naver-rider"] = RunStatus.PENDING.value
    if isinstance(executions, dict):
        executions["naver-rider"] = StageExecution.NOT_CALLED.value
    atomic_write_json(state_path, state)
    return RunnerRequest(
        root=root,
        job="",
        run_id=run_id,
        state_dir=state_dir,
        executor=confirmation_input.executor,
        notion_adapter=confirmation_input.notion_adapter,
        naver_adapter=confirmation_input.naver_adapter,
    )


__all__ = ["apply_confirmation"]
