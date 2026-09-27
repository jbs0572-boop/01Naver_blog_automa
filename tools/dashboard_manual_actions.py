from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

from tools.contract_types import ContractError
from tools.dashboard_manual_models import (
    ManualActionView,
    ManualRunContext,
    ManualRunDependencies,
    ManualRunView,
)
from tools.dashboard_manual_request import confirmation_preview
from tools.runner_execution import confirm_job, resume_job
from tools.runner_records import q1_retry_exhausted
from tools.runner_state import read_state, state_paths
from tools.runner_types import ConfirmationInput, RunnerRequest, RunStatus


def execute_child_action(
    context: ManualRunContext,
    dependencies: ManualRunDependencies,
    child: ManualRunView,
    kind: Literal["retry", "external", "confirm"],
) -> ManualRunView:
    match kind:
        case "retry":
            result = dependencies.runner(_initial_request(context, dependencies, child))
        case "external":
            if (
                dependencies.notion_adapter is None
                and dependencies.naver_adapter is None
            ):
                return replace(
                    child,
                    status="completed",
                    message="외부 저장 대기 · Notion/Naver 연결이 필요합니다",
                    next_action=ManualActionView("external", uuid.uuid4().hex),
                    updated_at=_now(),
                )
            result = resume_job(
                RunnerRequest(
                    root=context.root,
                    job="daily-generate",
                    run_id=child.run_id,
                    dry_run=not context.live_writes,
                    executor=dependencies.executor,
                    notion_adapter=(
                        dependencies.notion_adapter if context.live_writes else None
                    ),
                    naver_adapter=(
                        dependencies.naver_adapter if context.live_writes else None
                    ),
                    resume=True,
                )
            )
        case "confirm":
            if (
                child.run_id is None
                or child.confirmation_preview is None
                or child.next_action is None
                or child.next_action.kind != "confirm"
            ):
                raise ContractError("manual child confirmation is incomplete")
            confirmation_action = child.next_action
            result = confirm_job(
                ConfirmationInput(
                    root=context.root,
                    run_id=child.run_id,
                    action=child.confirmation_preview.action,
                    executor=dependencies.executor,
                    notion_adapter=dependencies.notion_adapter,
                    naver_adapter=dependencies.naver_adapter,
                    confirmation_nonce=confirmation_action.nonce,
                )
            )
    if result.run_id != child.run_id:
        raise ContractError("child action returned a different preallocated run_id")
    preview = confirmation_preview(result)
    next_action = None
    if result.status is RunStatus.LOCAL_ONLY:
        next_action = ManualActionView("external", uuid.uuid4().hex)
    elif result.status is RunStatus.AWAITING_USER_CONFIRMATION:
        state = read_state(result.state_path)
        confirmation_nonce = state.get("confirmation_nonce")
        if not isinstance(confirmation_nonce, str):
            raise ContractError("runner confirmation nonce is missing")
        next_action = ManualActionView("confirm", confirmation_nonce)
    elif result.status is RunStatus.READY_FOR_NAVER:
        next_action = ManualActionView("external", uuid.uuid4().hex)
    elif result.status is RunStatus.FAILED and not _q1_exhausted(context, child):
        next_action = ManualActionView("retry", uuid.uuid4().hex)
    return replace(
        child,
        status="failed" if result.status is RunStatus.FAILED else "completed",
        result_status=result.status.value,
        message=result.message,
        error=None,
        retryable=result.status is RunStatus.FAILED and next_action is not None,
        confirmation_preview=preview,
        next_action=next_action,
        updated_at=_now(),
    )


def recover_child_action(
    context: ManualRunContext,
    dependencies: ManualRunDependencies,
    child: ManualRunView,
    kind: Literal["retry", "external", "confirm"],
) -> ManualRunView:
    if kind != "confirm":
        return execute_child_action(context, dependencies, child, kind)
    if child.run_id is None:
        raise ContractError("manual child confirmation recovery has no run_id")
    result = resume_job(
        RunnerRequest(
            root=context.root,
            job="daily-generate",
            run_id=child.run_id,
            dry_run=not context.live_writes,
            executor=dependencies.executor,
            notion_adapter=dependencies.notion_adapter,
            naver_adapter=dependencies.naver_adapter,
            resume=True,
        )
    )
    if result.run_id != child.run_id:
        raise ContractError("confirmation recovery changed child run_id")
    return replace(
        child,
        status="failed" if result.status is RunStatus.FAILED else "completed",
        result_status=result.status.value,
        message=result.message,
        active_action=None,
        updated_at=_now(),
    )


def _initial_request(
    context: ManualRunContext,
    dependencies: ManualRunDependencies,
    child: ManualRunView,
) -> RunnerRequest:
    return RunnerRequest(
        root=context.root,
        job="daily-generate",
        keyword=child.requested_keyword,
        run_id=child.run_id,
        dry_run=False,
        auto_topic=child.requested_keyword is None,
        selection_context=child.selection_context,
        model_config=child.model_config,
        executor=dependencies.executor,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _q1_exhausted(context: ManualRunContext, child: ManualRunView) -> bool:
    if child.run_id is None:
        return False
    state_path, _, _ = state_paths(context.root, child.run_id)
    if not state_path.is_file():
        return False
    return q1_retry_exhausted(read_state(state_path), context.root, child.run_id)


__all__ = ["execute_child_action", "recover_child_action"]
