from __future__ import annotations

import hashlib
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Literal, final

from tools.contract_types import ContractError, JSONMap
from tools.dashboard_manual_actions import execute_child_action, recover_child_action
from tools.dashboard_manual_batch import aggregate_status, new_batch, prepare_child
from tools.dashboard_manual_models import (
    ConfirmationPreview,
    ManualActionView,
    ManualActiveActionView,
    ManualBatchView,
    ManualCancellationView,
    ManualRunContext,
    ManualRunDependencies,
    ManualRunInput,
    ManualRunView,
    Runner,
)
from tools.dashboard_manual_request import (
    confirmation_preview,
    parse_manual_run_payload,
)
from tools.dashboard_manual_store import ManualBatchStore
from tools.run_cancellation import (
    CancellationScope,
    create_cancellation,
    nonce_digest,
    read_cancellation,
)
from tools.runner_execution import invalidate_naver_preparation, recover_job
from tools.runner_notion_action import configured_notion_target
from tools.runner_records import q1_retry_exhausted
from tools.runner_state import read_state, state_paths
from tools.runner_types import RunnerRequest, RunStatus

type ActionKind = Literal["retry", "external", "confirm"]
RECOVERY_STALE_AFTER = timedelta(minutes=15)


def _naver_save_outcome_uncertain(root: Path, run_id: str | None) -> bool:
    if run_id is None:
        return True
    try:
        state_path, _, _ = state_paths(root, run_id)
        state = read_state(state_path)
    except ContractError:
        return True
    return state.get("naver_save_outcome_uncertain") is True


def _confirmed_naver_save_state(root: Path, run_id: str | None) -> JSONMap | None:
    if run_id is None:
        return None
    try:
        state_path, _, _ = state_paths(root, run_id)
        state = read_state(state_path)
    except ContractError:
        return None
    if (
        state.get("run_id") != run_id
        or state.get("status") != RunStatus.DRAFT_SAVED.value
    ):
        return None
    return state


def is_stale_recovery(updated_at: str, *, now: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(updated_at)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return now - parsed.astimezone(UTC) > RECOVERY_STALE_AFTER


@final
class ManualRunManager:
    def __init__(self, context: ManualRunContext, dependencies: ManualRunDependencies) -> None:
        self._context = context
        self._dependencies = dependencies
        self._store = ManualBatchStore(context.root)
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qa-runner")
        self._futures: list[Future[None]] = []
        self._recover()

    def start(self, request: ManualRunInput) -> ManualBatchView:
        if request.request_nonce is None:
            request = replace(request, request_nonce=str(uuid.uuid4()))
        batch, created = self._store.accept(
            request,
            lambda display_id: new_batch(
                request, self._context.root, display_id=display_id
            ),
        )
        if created:
            self._futures.append(
                self._pool.submit(self.execute_initial, batch.batch_id, 1)
            )
        return batch

    def get(self, batch_id: str) -> ManualBatchView | None:
        batch = self._store.get(batch_id)
        if batch is not None and batch.status == "completed":
            return replace(batch, status=aggregate_status(batch))
        return batch

    def list(self, limit: int = 20) -> tuple[ManualBatchView, ...]:
        return tuple(replace(batch, status=aggregate_status(batch)) if batch.status == "completed" else batch for batch in self._store.list(limit))

    def snapshot(self) -> tuple[ManualBatchView, ...]:
        return tuple(replace(batch, status=aggregate_status(batch)) for batch in self._store.snapshot())

    def close(self) -> None:
        self._pool.shutdown(wait=True)

    def has_active_work(self) -> bool:
        self._futures = [future for future in self._futures if not future.done()]
        return bool(self._futures)

    def cancel(
        self, batch_id: str, child_id: str, nonce: str, scope: CancellationScope
    ) -> tuple[ManualBatchView, bool]:
        with self._lock:
            batch = self._required_batch(batch_id)
            if len(batch.children) != 1:
                raise ContractError("retired manual batch is read-only")
            child = self._required_child(batch, child_id)
            if child.cancellation is not None and child.cancellation.scope == scope and child.cancellation.nonce_sha256 == nonce_digest(nonce):
                return batch, child.status == "cancelling"
            action = child.cancel_action
            if action is None or action.nonce != nonce or action.scope != scope:
                raise ContractError("cancellation request is stale or invalid")
            if scope == "queued_only" and child.status != "queued":
                raise ContractError("queued_only cancellation requires a queued child")
            if child.status in {"completed", "failed", "cancelled"}:
                raise ContractError("manual child is already settled")
            request = create_cancellation(
                self._context.root,
                run_id=child.run_id or "",
                batch_id=batch_id,
                child_id=child_id,
                scope=scope,
                nonce=nonce,
            )
            accepted_at = request.requested_at
            if child.status == "queued":
                updated = replace(
                    child,
                    status="cancelled",
                    result_status="cancelled",
                    message="취소됨",
                    cancellation=ManualCancellationView(scope, accepted_at, request.nonce_sha256, accepted_at),
                    cancel_action=None,
                    next_action=None,
                    active_action=None,
                    updated_at=accepted_at,
                )
                accepted = False
            else:
                updated = replace(
                    child,
                    status="cancelling",
                    message="중단 요청됨 · 현재 단계 정리 후 중단",
                    cancellation=ManualCancellationView(scope, accepted_at, request.nonce_sha256),
                    cancel_action=None,
                    updated_at=accepted_at,
                )
                accepted = True
            updated_batch = _replace_child(batch, updated)
            updated_batch = replace(updated_batch, status=aggregate_status(updated_batch), updated_at=accepted_at)
            self._store.save(updated_batch)
            return updated_batch, accepted

    def cancel_task(self, task_id: str, nonce: str, scope: CancellationScope) -> tuple[ManualBatchView, bool]:
        batch, child = self._find_task(task_id)
        if child.child_id is None:
            raise ContractError("manual child not found")
        return self.cancel(batch.batch_id, child.child_id, nonce, scope)

    def submit_action(
        self, batch_id: str, child_id: str, kind: ActionKind, nonce: str
    ) -> ManualBatchView:
        with self._lock:
            batch = self._required_batch(batch_id)
            if len(batch.children) != 1:
                raise ContractError("retired manual batch is read-only")
            child = self._required_child(batch, child_id)
            action = child.next_action
            if action is None or action.kind != kind or action.nonce != nonce:
                raise ContractError("manual child action is stale or invalid")
            if kind == "retry" and child.run_id is not None:
                state_path, _, _ = state_paths(self._context.root, child.run_id)
                if state_path.is_file() and q1_retry_exhausted(
                    read_state(state_path), self._context.root, child.run_id
                ):
                    raise ContractError(
                        "Q1 retry limit exhausted; no further producer call permitted"
                    )
            accepted_at = _now()
            updated = replace(
                child,
                next_action=None,
                status="queued",
                active_action=ManualActiveActionView(
                    kind,
                    "OP-" + uuid.uuid4().hex[:12],
                    "sha256:" + hashlib.sha256(nonce.encode()).hexdigest(),
                    accepted_at,
                    "queued",
                ),
                updated_at=accepted_at,
            )
            batch = _replace_child(batch, updated)
            batch = replace(batch, status=aggregate_status(batch), updated_at=accepted_at)
            self._store.save(batch)
        self._futures.append(
            self._pool.submit(self._execute_action, batch_id, child_id, kind, child)
        )
        return batch

    def continue_external(self, task_id: str) -> ManualRunView:
        batch, child = self._find_task(task_id)
        if len(batch.children) != 1:
            raise ContractError("retired manual batch is read-only")
        if child.next_action is None or child.next_action.kind != "external":
            raise ContractError("manual child action is stale or invalid")
        updated = execute_child_action(
            self._context, self._dependencies, child, "external"
        )
        self._save_child(batch, updated)
        return updated

    def confirm(self, task_id: str) -> ManualRunView:
        batch, child = self._find_task(task_id)
        nonce = child.next_action.nonce if child.next_action else ""
        _ = self.submit_action(batch.batch_id, str(child.child_id), "confirm", nonce)
        self.close()
        return self._required_child(self._required_batch(batch.batch_id), str(child.child_id))

    def execute_initial(self, batch_id: str, slot: int) -> None:
        self._execute_run(batch_id, slot, self._dependencies.runner)

    def _execute_retry(self, batch_id: str, slot: int) -> None:
        try:
            if self._renew_failed_naver_preparation(batch_id, slot):
                return
        except (ContractError, OSError, TimeoutError, ValueError) as error:
            self._settle_error(batch_id, slot, type(error).__name__)
            return
        self._execute_run(
            batch_id, slot, self._dependencies.recoverer or recover_job
        )

    def _renew_failed_naver_preparation(self, batch_id: str, slot: int) -> bool:
        batch = self._required_batch(batch_id)
        child = batch.children[slot - 1]
        if child.run_id is None:
            return False
        state_path, _, _ = state_paths(self._context.root, child.run_id)
        if not state_path.is_file():
            return False
        state = read_state(state_path)
        stages = state.get("stages")
        if not (
            state.get("status") == RunStatus.FAILED.value
            and isinstance(state.get("confirmation"), dict)
            and isinstance(stages, dict)
            and stages.get("naver-rider") == RunStatus.FAILED.value
        ):
            return False
        invalidate_naver_preparation(self._context.root, child.run_id)
        refreshed = read_state(state_path)
        message = refreshed.get("message")
        keyword = refreshed.get("keyword")
        self._settle_result(
            batch_id,
            slot,
            RunStatus.LOCAL_ONLY,
            message if isinstance(message, str) else "Naver preparation must be renewed",
            keyword if isinstance(keyword, str) else child.resolved_keyword,
            None,
            None,
        )
        return True

    def _execute_run(self, batch_id: str, slot: int, runner: Runner) -> None:
        with self._lock:
            batch = self._required_batch(batch_id)
            child = batch.children[slot - 1]
            if read_cancellation(self._context.root, child.run_id or "") is not None:
                updated = replace(child, status="cancelled", result_status="cancelled", message="취소됨", cancel_action=None, updated_at=_now())
                updated_batch = _replace_child(batch, updated)
                self._store.save(replace(updated_batch, status=aggregate_status(updated_batch), updated_at=updated.updated_at))
                return
            active_cancel = child.cancel_action
            if active_cancel is not None:
                active_cancel = replace(active_cancel, scope="remaining")
            started_at = child.started_at or _now()
            child = replace(
                prepare_child(batch, slot),
                active_action=None,
                status="running",
                cancel_action=active_cancel,
                started_at=started_at,
                updated_at=started_at,
            )
            batch = _replace_child(batch, child)
            self._store.save(replace(batch, status=aggregate_status(batch), updated_at=child.updated_at))
        try:
            result = runner(self._request(child))
            if result.run_id != child.run_id:
                raise ContractError("runner returned a different preallocated run_id")
            if result.status is RunStatus.DRAFT_SAVED:
                raise ContractError("initial or retry runner cannot save a Naver draft")
            state = read_state(result.state_path) if result.state_path.is_file() else {}
            keyword = state.get("keyword")
            digest = state.get("selection_snapshot_sha256")
            if read_cancellation(self._context.root, child.run_id or "") is not None:
                self._settle_cancelled(batch_id, slot, result.message)
                return
            self._settle_result(
                batch_id,
                slot,
                result.status,
                result.message,
                keyword if isinstance(keyword, str) else None,
                digest if isinstance(digest, str) else None,
                confirmation_preview(result),
            )
        except (ContractError, OSError, TimeoutError, ValueError) as error:
            if read_cancellation(self._context.root, child.run_id or "") is not None:
                self._settle_cancelled(batch_id, slot, str(error))
                return
            self._settle_error(batch_id, slot, type(error).__name__)

    def _settle_cancelled(self, batch_id: str, slot: int, message: str) -> None:
        batch = self._required_batch(batch_id)
        child = batch.children[slot - 1]
        request = read_cancellation(self._context.root, child.run_id or "")
        cancellation = child.cancellation
        if request is not None:
            cancellation = ManualCancellationView(request.scope, request.requested_at, request.nonce_sha256, _now())
        settled_at = _now()
        self._finish_child(batch, replace(child, status="cancelled", result_status="cancelled", message=message or "취소됨", cancellation=cancellation, cancel_action=None, next_action=None, active_action=None, retryable=False, updated_at=settled_at, ended_at=settled_at))

    def _request(self, child: ManualRunView) -> RunnerRequest:
        return RunnerRequest(
            root=self._context.root,
            job="daily-generate",
            keyword=child.requested_keyword,
            run_id=child.run_id,
            auto_topic=child.requested_keyword is None,
            selection_context=child.selection_context,
            model_config=child.model_config,
            dry_run=False,
            executor=self._dependencies.executor,
            notion_target_id=(
                configured_notion_target(self._context.root)
                if self._context.live_writes
                and self._dependencies.notion_adapter is not None
                else None
            ),
        )

    def _settle_result(
        self,
        batch_id: str,
        slot: int,
        status: RunStatus,
        message: str,
        resolved: str | None,
        digest: str | None,
        preview: ConfirmationPreview | None,
    ) -> None:
        batch = self._required_batch(batch_id)
        snapshot = batch.snapshot
        if snapshot is not None and digest is not None:
            snapshot = replace(snapshot, sha256=_prefixed_sha256(digest))
            batch = replace(batch, snapshot=snapshot)
        child = batch.children[slot - 1]
        next_action = None
        if status is RunStatus.LOCAL_ONLY:
            next_action = ManualActionView("external", uuid.uuid4().hex)
        elif status is RunStatus.FAILED and not self._q1_exhausted(child):
            next_action = ManualActionView("retry", uuid.uuid4().hex)
        child = replace(
            child,
            status="failed" if status is RunStatus.FAILED else "completed",
            result_status=status.value,
            message=message,
            resolved_keyword=resolved,
            keyword=resolved or child.keyword,
            retryable=status is RunStatus.FAILED and next_action is not None,
            next_action=next_action,
            confirmation_preview=preview,
            active_action=None,
            updated_at=_now(),
            ended_at=_now(),
        )
        self._finish_child(batch, child)

    def _settle_error(self, batch_id: str, slot: int, error: str) -> None:
        batch = self._required_batch(batch_id)
        retryable = not self._q1_exhausted(batch.children[slot - 1])
        child = replace(
            batch.children[slot - 1],
            status="failed",
            error=error,
            retryable=retryable,
            next_action=(ManualActionView("retry", uuid.uuid4().hex) if retryable else None),
            updated_at=_now(),
            ended_at=_now(),
        )
        self._finish_child(batch, child)

    def _finish_child(self, batch: ManualBatchView, child: ManualRunView) -> None:
        with self._lock:
            current = self._required_batch(batch.batch_id)
            if batch.snapshot is not None:
                current = replace(current, snapshot=batch.snapshot)
            batch = _replace_child(current, child)
            batch = replace(batch, status=aggregate_status(batch), updated_at=_now())
            self._store.save(batch)
        slot = child.slot or 1
        if child.resolved_keyword is not None and slot < len(batch.children):
            self._resume_persisted_child(batch.batch_id, slot + 1)

    def _execute_action(
        self,
        batch_id: str,
        child_id: str,
        kind: ActionKind,
        accepted_child: ManualRunView,
    ) -> None:
        with self._lock:
            batch = self._required_batch(batch_id)
            child = self._required_child(batch, child_id)
            active_action = child.active_action
            if child.status == "cancelled":
                return
            if (
                child.status != "queued"
                or active_action is None
                or active_action.kind != kind
            ):
                return
            started_at = _now()
            child = replace(
                child,
                status="running",
                active_action=replace(active_action, state="running"),
                updated_at=started_at,
            )
            batch = _replace_child(batch, child)
            batch = replace(
                batch,
                status=aggregate_status(batch),
                updated_at=started_at,
            )
            self._store.save(batch)
        if kind == "retry":
            if child.slot is None:
                self._settle_error(batch_id, 1, "ContractError")
                return
            self._execute_retry(batch_id, child.slot)
            return
        try:
            updated = execute_child_action(
                self._context, self._dependencies, accepted_child, kind
            )
        except (ContractError, OSError, TimeoutError, ValueError) as error:
            uncertain_save = kind == "confirm" and _naver_save_outcome_uncertain(
                self._context.root, accepted_child.run_id
            )
            settled_at = _now()
            updated = replace(
                child,
                status="failed",
                result_status=RunStatus.FAILED.value,
                error=type(error).__name__,
                message=(
                    "네이버 임시저장 결과가 불확실합니다. "
                    + "중복 저장 방지를 위해 임시저장 목록을 수동 대조해야 합니다."
                    if uncertain_save
                    else child.message
                ),
                retryable=not uncertain_save,
                next_action=(
                    None
                    if uncertain_save
                    else ManualActionView("retry", uuid.uuid4().hex)
                ),
                updated_at=settled_at,
                ended_at=settled_at,
            )
        self._save_child(batch, replace(updated, active_action=None))

    def _recover_action(self, batch_id: str, child_id: str, kind: ActionKind) -> None:
        batch = self._required_batch(batch_id)
        child = self._required_child(batch, child_id)
        try:
            updated = recover_child_action(self._context, self._dependencies, child, kind)
        except (ContractError, OSError, TimeoutError, ValueError) as error:
            uncertain_save = kind == "confirm" and _naver_save_outcome_uncertain(
                self._context.root, child.run_id
            )
            settled_at = _now()
            updated = replace(
                child,
                status="failed",
                result_status=RunStatus.FAILED.value,
                message=(
                    "네이버 임시저장 결과 확인이 필요합니다. "
                    + "중복 저장 방지를 위해 수동 대조 후 재개해 주세요."
                    if uncertain_save
                    else "대시보드가 중단된 작업을 복구하지 못했습니다. 기록을 확인해 주세요."
                ),
                error=type(error).__name__,
                retryable=not uncertain_save,
                next_action=(
                    None
                    if uncertain_save
                    else ManualActionView("retry", uuid.uuid4().hex)
                ),
                confirmation_preview=None if uncertain_save else child.confirmation_preview,
                active_action=None,
                updated_at=settled_at,
                ended_at=settled_at,
            )
        self._save_child(batch, replace(updated, active_action=None))

    def _save_child(self, batch: ManualBatchView, child: ManualRunView) -> None:
        cancellation_slot: int | None = None
        with self._lock:
            current = self._required_batch(batch.batch_id)
            if child.child_id is None:
                return
            persisted = self._required_child(current, child.child_id)
            if persisted.status == "cancelled":
                return
            cancellation_requested = persisted.status == "cancelling"
            if not cancellation_requested and persisted.run_id is not None:
                cancellation_requested = (
                    read_cancellation(self._context.root, persisted.run_id) is not None
                )
            if cancellation_requested:
                uncertain_save = (
                    child.result_status == RunStatus.FAILED.value
                    and persisted.active_action is not None
                    and persisted.active_action.kind == "confirm"
                    and _naver_save_outcome_uncertain(
                        self._context.root, persisted.run_id
                    )
                )
                if (
                    child.result_status == RunStatus.DRAFT_SAVED.value
                    or uncertain_save
                ):
                    settled_at = _now()
                    request = (
                        read_cancellation(self._context.root, persisted.run_id)
                        if persisted.run_id is not None
                        else None
                    )
                    cancellation = persisted.cancellation
                    if request is not None:
                        cancellation = ManualCancellationView(
                            request.scope,
                            request.requested_at,
                            request.nonce_sha256,
                            settled_at,
                        )
                    elif cancellation is not None:
                        cancellation = replace(cancellation, completed_at=settled_at)
                    settled_child = replace(
                        child,
                        message=(
                            "네이버 임시저장 완료 · 취소 요청 처리 중 저장 결과를 보존했습니다."
                            if child.result_status == RunStatus.DRAFT_SAVED.value
                            else child.message
                        ),
                        cancellation=cancellation,
                        cancel_action=None,
                        next_action=None,
                        active_action=None,
                        retryable=False,
                        updated_at=settled_at,
                        ended_at=child.ended_at or settled_at,
                    )
                    updated = _replace_child(current, settled_child)
                    self._store.save(
                        replace(
                            updated,
                            status=aggregate_status(updated),
                            updated_at=settled_at,
                        )
                    )
                else:
                    cancellation_slot = persisted.slot or 1
            else:
                updated = _replace_child(current, child)
                self._store.save(
                    replace(updated, status=aggregate_status(updated), updated_at=_now())
                )
        if cancellation_slot is not None:
            self._settle_cancelled(batch.batch_id, cancellation_slot, "취소됨")

    def _q1_exhausted(self, child: ManualRunView) -> bool:
        if child.run_id is None:
            return False
        state_path, _, _ = state_paths(self._context.root, child.run_id)
        if not state_path.is_file():
            return False
        return q1_retry_exhausted(read_state(state_path), self._context.root, child.run_id)

    def _resume_persisted_child(self, batch_id: str, slot: int) -> None:
        batch = self._required_batch(batch_id)
        child = batch.children[slot - 1]
        if child.status in {"cancelled", "cancelling"} or read_cancellation(self._context.root, child.run_id or "") is not None:
            if child.status == "queued":
                self._settle_cancelled(batch_id, slot, "취소됨")
            return
        active = child.active_action
        if active is not None:
            match active.kind:
                case "initial":
                    self.execute_initial(batch_id, slot)
                case "retry":
                    self._execute_retry(batch_id, slot)
                case "external" | "confirm":
                    if child.child_id is not None:
                        self._recover_action(batch_id, child.child_id, active.kind)
            return
        match child.status:
            case "queued":
                self.execute_initial(batch_id, slot)
            case "running":
                self._execute_retry(batch_id, slot)
            case "completed":
                if child.result_status == RunStatus.RUNNING.value:
                    self._execute_retry(batch_id, slot)
            case "failed":
                return
            case "cancelling" | "cancelled":
                return

    def _recover(self) -> None:
        for batch in self._store.list(1000):
            if len(batch.children) != 1:
                continue
            now_dt = datetime.now(UTC)
            now = now_dt.isoformat()
            confirmed_results: dict[str, tuple[str, str]] = {}
            for child in batch.children:
                if child.result_status != RunStatus.AWAITING_USER_CONFIRMATION.value:
                    continue
                state = _confirmed_naver_save_state(self._context.root, child.run_id)
                if state is not None and child.run_id is not None:
                    message = state.get("message")
                    updated_at = state.get("updated_at")
                    saved_at = now
                    if isinstance(updated_at, str):
                        try:
                            parsed_updated_at = datetime.fromisoformat(updated_at)
                        except ValueError:
                            pass
                        else:
                            if parsed_updated_at.tzinfo is not None:
                                saved_at = parsed_updated_at.astimezone(UTC).isoformat()
                    confirmed_results[child.run_id] = (
                        message
                        if isinstance(message, str)
                        else "네이버 임시저장이 완료됐습니다.",
                        saved_at,
                    )
            if confirmed_results:
                recovered_children = tuple(
                    replace(
                        child,
                        status="completed",
                        result_status=RunStatus.DRAFT_SAVED.value,
                        message=confirmed_results[child.run_id or ""][0],
                        error=None,
                        retryable=False,
                        confirmation_preview=None,
                        next_action=None,
                        active_action=None,
                        cancel_action=None,
                        cancellation=(
                            replace(child.cancellation, completed_at=now)
                            if child.cancellation is not None
                            and child.cancellation.completed_at is None
                            else child.cancellation
                        ),
                        updated_at=now,
                        ended_at=confirmed_results[child.run_id or ""][1],
                    )
                    if child.run_id in confirmed_results
                    else child
                    for child in batch.children
                )
                batch = replace(
                    batch,
                    children=recovered_children,
                    status=aggregate_status(replace(batch, children=recovered_children)),
                    updated_at=now,
                )
                self._store.save(batch)
            uncertain_runs = {
                child.run_id
                for child in batch.children
                if child.result_status
                == RunStatus.AWAITING_USER_CONFIRMATION.value
                and _naver_save_outcome_uncertain(self._context.root, child.run_id)
            }
            if uncertain_runs:
                recovered_children = tuple(
                    replace(
                        child,
                        status="failed",
                        result_status=RunStatus.FAILED.value,
                        message=(
                            "네이버 임시저장 결과 확인이 필요합니다. "
                            "중복 저장 방지를 위해 수동 대조 후 재개해 주세요."
                        ),
                        error="NaverSaveReconciliationRequired",
                        retryable=False,
                        confirmation_preview=None,
                        next_action=None,
                        active_action=None,
                        updated_at=now,
                    )
                    if child.run_id in uncertain_runs
                    else child
                    for child in batch.children
                )
                batch = replace(
                    batch,
                    children=recovered_children,
                    status=aggregate_status(replace(batch, children=recovered_children)),
                    updated_at=now,
                )
                self._store.save(batch)
            recovered_children = tuple(
                replace(
                    child,
                    status="failed",
                    result_status=RunStatus.FAILED.value,
                    message=(
                        "The workflow was interrupted and needs an explicit retry "
                        "to resume from its saved runner state."
                    ),
                    error="StaleRunRecovery",
                    retryable=True,
                    next_action=ManualActionView("retry", uuid.uuid4().hex),
                    active_action=None,
                    updated_at=now,
                    ended_at=now,
                )
                if child.status in {"queued", "running"}
                and is_stale_recovery(child.updated_at, now=now_dt)
                else child
                for child in batch.children
            )
            if recovered_children != batch.children:
                batch = replace(
                    batch,
                    children=recovered_children,
                    status=aggregate_status(replace(batch, children=recovered_children)),
                    updated_at=now,
                )
                self._store.save(batch)
            if self._context.live_writes and self._dependencies.naver_adapter is not None:
                recovered_children = tuple(
                    replace(
                        child,
                        status="failed",
                        result_status=RunStatus.FAILED.value,
                        message=(
                            "네이버 임시저장 결과 확인이 필요합니다. "
                            "중복 저장 방지를 위해 수동 대조 후 재개해 주세요."
                        ),
                        error="NaverSaveReconciliationRequired",
                        retryable=False,
                        confirmation_preview=None,
                        next_action=None,
                        active_action=None,
                        updated_at=_now(),
                    )
                    if child.result_status
                    == RunStatus.AWAITING_USER_CONFIRMATION.value
                    and child.run_id in uncertain_runs
                    else replace(
                        child,
                        result_status=RunStatus.LOCAL_ONLY.value,
                        message="Naver preparation must be renewed after dashboard restart",
                        confirmation_preview=None,
                        next_action=ManualActionView("external", uuid.uuid4().hex),
                        active_action=None,
                        updated_at=_now(),
                    )
                    if child.result_status
                    == RunStatus.AWAITING_USER_CONFIRMATION.value
                    else child
                    for child in batch.children
                )
                if recovered_children != batch.children:
                    for child in batch.children:
                        if (
                            child.result_status
                            == RunStatus.AWAITING_USER_CONFIRMATION.value
                            and child.run_id is not None
                            and child.run_id not in uncertain_runs
                        ):
                            invalidate_naver_preparation(
                                self._context.root, child.run_id
                            )
                    batch = replace(
                        batch,
                        children=recovered_children,
                        status=aggregate_status(replace(batch, children=recovered_children)),
                        updated_at=_now(),
                    )
                    self._store.save(batch)
            batch = self._required_batch(batch.batch_id)
            for child in batch.children:
                if child.status == "cancelling" and child.slot is not None:
                    self._settle_cancelled(batch.batch_id, child.slot, "취소됨")
                    batch = self._required_batch(batch.batch_id)
            action_child = next(
                (child for child in batch.children if child.active_action is not None),
                None,
            )
            if action_child is not None and action_child.slot is not None:
                self._futures.append(self._pool.submit(
                    self._resume_persisted_child, batch.batch_id, action_child.slot
                ))
                continue
            pending = next(
                (
                    child
                    for child in batch.children
                    if (
                        child.status in {"queued", "running"}
                        or child.status == "completed"
                        and child.result_status == RunStatus.RUNNING.value
                    )
                    and child.slot is not None
                    and all(
                        previous.resolved_keyword is not None
                        for previous in batch.children[: child.slot - 1]
                    )
                ),
                None,
            )
            if pending is not None and pending.slot is not None:
                self._futures.append(self._pool.submit(
                    self._resume_persisted_child, batch.batch_id, pending.slot
                ))

    def _required_batch(self, batch_id: str) -> ManualBatchView:
        batch = self._store.get(batch_id)
        if batch is None:
            raise ContractError("manual batch not found")
        return batch

    @staticmethod
    def _required_child(batch: ManualBatchView, child_id: str) -> ManualRunView:
        for child in batch.children:
            if child.child_id == child_id:
                return child
        raise ContractError("manual child not found")

    def _find_task(self, task_id: str) -> tuple[ManualBatchView, ManualRunView]:
        for batch in self._store.list(1000):
            for child in batch.children:
                if child.task_id == task_id:
                    return batch, child
        raise ContractError("manual run task not found")


def _replace_child(batch: ManualBatchView, child: ManualRunView) -> ManualBatchView:
    children = tuple(
        child if item.child_id == child.child_id else item for item in batch.children
    )
    return replace(batch, children=children)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _prefixed_sha256(value: str) -> str:
    return value if value.startswith("sha256:") else f"sha256:{value}"


__all__ = [
    "ManualRunContext",
    "ManualRunDependencies",
    "ManualRunInput",
    "ManualRunManager",
    "ManualRunView",
    "parse_manual_run_payload",
]
