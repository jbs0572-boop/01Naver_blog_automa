from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Lock

from tools.contract_types import ContractError
from tools.dashboard_manual_models import (
    ManualRunContext,
    ManualRunDependencies,
    ManualRunInput,
    ManualRunUpdate,
    ManualRunView,
    Runner,
)
from tools.dashboard_manual_request import (
    confirmation_preview,
    demo_runner,
    parse_manual_run_payload,
)
from tools.runner_execution import confirm_job, resume_job
from tools.runner_notion_action import configured_notion_target
from tools.runner_types import (
    ConfirmationInput,
    RunnerRequest,
    RunStatus,
)


class ManualRunManager:
    def __init__(
        self,
        context: ManualRunContext,
        dependencies: ManualRunDependencies,
    ) -> None:
        self._context: ManualRunContext = context
        self._dependencies: ManualRunDependencies = dependencies
        self._runner: Runner = demo_runner if context.demo else dependencies.runner
        self._lock: Lock = Lock()
        self._tasks: dict[str, ManualRunView] = {}
        self._pool: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="qa-runner"
        )

    def start(self, request: ManualRunInput) -> ManualRunView:
        now = datetime.now(UTC).isoformat()
        task_id = "TASK-" + uuid.uuid4().hex[:12]
        view = ManualRunView(task_id, "queued", now, now)
        with self._lock:
            self._tasks[task_id] = view
        _ = self._pool.submit(self._execute, task_id, request)
        return view

    def get(self, task_id: str) -> ManualRunView | None:
        with self._lock:
            return self._tasks.get(task_id)

    def close(self) -> None:
        _ = self._pool.shutdown(wait=True)

    def confirm(self, task_id: str) -> ManualRunView:
        view = self.get(task_id)
        if view is None:
            raise ContractError("manual run task not found")
        if view.result_status != RunStatus.AWAITING_USER_CONFIRMATION.value:
            raise ContractError("manual run is not awaiting user confirmation")
        if view.run_id is None:
            raise ContractError("manual run confirmation has no run_id")
        if view.confirmation_preview is None:
            raise ContractError("manual run confirmation preview is missing")
        try:
            result = confirm_job(
                ConfirmationInput(
                    root=self._context.root,
                    run_id=view.run_id,
                    action=view.confirmation_preview.action,
                    executor=self._dependencies.executor,
                    notion_adapter=self._dependencies.notion_adapter,
                    naver_adapter=self._dependencies.naver_adapter,
                )
            )
        except (ContractError, OSError, TimeoutError, ValueError) as error:
            self._update(task_id, ManualRunUpdate(status="failed", error=type(error).__name__, retryable=True))
            raise
        self._update(
            task_id,
            ManualRunUpdate(
                status="completed",
                run_id=result.run_id,
                result_status=result.status.value,
                message=result.message,
                retryable=result.status is RunStatus.FAILED,
            ),
        )
        updated = self.get(task_id)
        if updated is None:
            raise ContractError("manual run disappeared after confirmation")
        return updated

    def continue_external(self, task_id: str) -> ManualRunView:
        view = self.get(task_id)
        if view is None:
            raise ContractError("manual run task not found")
        if view.run_id is None:
            raise ContractError("external storage requires a completed run")
        if (
            self._dependencies.notion_adapter is None
            and self._dependencies.naver_adapter is None
        ):
            self._update(
                task_id,
                ManualRunUpdate(message="외부 저장 대기 · Notion/Naver 연결이 필요합니다"),
            )
            updated = self.get(task_id)
            if updated is None:
                raise ContractError("manual run disappeared while updating status")
            return updated
        try:
            result = resume_job(
                RunnerRequest(
                    root=self._context.root,
                    job="daily-generate",
                    run_id=view.run_id,
                    dry_run=not self._context.live_writes,
                    executor=self._dependencies.executor,
                    notion_adapter=(
                        self._dependencies.notion_adapter
                        if self._context.live_writes
                        else None
                    ),
                    naver_adapter=(
                        self._dependencies.naver_adapter
                        if self._context.live_writes
                        else None
                    ),
                    resume=True,
                )
            )
            preview = confirmation_preview(result)
        except (ContractError, OSError, TimeoutError, ValueError) as error:
            self._update(task_id, ManualRunUpdate(status="failed", error=type(error).__name__, retryable=True))
            raise
        self._update(
            task_id,
            ManualRunUpdate(
                status="completed",
                run_id=result.run_id,
                result_status=result.status.value,
                message=result.message,
                retryable=result.status is RunStatus.FAILED,
                confirmation_preview=preview,
            ),
        )
        updated = self.get(task_id)
        if updated is None:
            raise ContractError("manual run disappeared after external storage")
        return updated

    def _execute(self, task_id: str, manual: ManualRunInput) -> None:
        self._update(task_id, ManualRunUpdate(status="running"))
        try:
            result = self._runner(
                RunnerRequest(
                    root=self._context.root,
                    job="daily-generate",
                    keyword=manual.keyword,
                    auto_topic=manual.auto_topic,
                    selection_context=manual.selection_context,
                    dry_run=False,
                    executor=self._dependencies.executor,
                    notion_target_id=(
                        configured_notion_target(self._context.root)
                        if (
                            self._context.live_writes
                            and self._dependencies.notion_adapter is not None
                        )
                        else None
                    ),
                    notion_adapter=None,
                    naver_adapter=None,
                )
            )
            preview = confirmation_preview(result)
        except (ContractError, OSError, TimeoutError, ValueError) as error:
            self._update(task_id, ManualRunUpdate(status="failed", error=type(error).__name__, retryable=False))
            return
        self._update(
            task_id,
            ManualRunUpdate(
                status="completed",
                run_id=result.run_id,
                result_status=result.status.value,
                message=result.message,
                retryable=False,
                confirmation_preview=preview,
            ),
        )

    def _update(
        self,
        task_id: str,
        update: ManualRunUpdate,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                return
            self._tasks[task_id] = replace(
                current,
                status=update.status or current.status,
                updated_at=now,
                run_id=update.run_id or current.run_id,
                result_status=update.result_status or current.result_status,
                message=update.message or current.message,
                error=update.error or current.error,
                retryable=(
                    update.retryable
                    if update.retryable is not None
                    else current.retryable
                ),
                confirmation_preview=(
                    update.confirmation_preview or current.confirmation_preview
                ),
            )


__all__ = [
    "ManualRunContext",
    "ManualRunDependencies",
    "ManualRunInput",
    "ManualRunManager",
    "ManualRunView",
    "parse_manual_run_payload",
]
