from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

import pytest

from tools.dashboard_manual_batch import new_batch
from tools.dashboard_manual_models import (
    ManualActionView,
    ManualRunContext,
    ManualRunDependencies,
    ManualRunView,
)
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.dashboard_manual_run import ManualRunManager
from tools.dashboard_manual_store import ManualBatchStore
from tools.runner_state import atomic_write_json, state_paths
from tools.runner_types import RunnerRequest, RunnerResult, RunStatus


def test_queued_cancel_prevents_runner_call(tmp_path: Path) -> None:
    calls: list[str | None] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        calls.append(request.run_id)
        return RunnerResult(request.run_id or "RUN-x", RunStatus.LOCAL_ONLY, tmp_path / "state", tmp_path / "log", (), "done")

    manager = ManualRunManager(ManualRunContext(tmp_path, False), ManualRunDependencies(runner))
    batch = new_batch(parse_manual_run_payload({"keyword": "대기 취소", "as_of_date": "2026-09-10"}))
    ManualBatchStore(tmp_path).save(batch)
    child = batch.children[0]
    assert child.cancel_action is not None
    updated, accepted = manager.cancel(
        batch.batch_id, child.child_id or "", child.cancel_action.nonce, "queued_only"
    )
    assert accepted is False
    assert updated.children[0].status == "cancelled"
    manager.execute_initial(batch.batch_id, 1)
    assert calls == []
    manager.close()


def test_running_cancel_settles_after_runner_returns(tmp_path: Path) -> None:
    started = Event()
    release = Event()

    def runner(request: RunnerRequest) -> RunnerResult:
        started.set()
        assert release.wait(2)
        return RunnerResult(request.run_id or "RUN-x", RunStatus.LOCAL_ONLY, tmp_path / "state", tmp_path / "log", (), "done")

    manager = ManualRunManager(ManualRunContext(tmp_path, False), ManualRunDependencies(runner))
    batch = new_batch(parse_manual_run_payload({"keyword": "실행 중단", "as_of_date": "2026-09-10"}))
    ManualBatchStore(tmp_path).save(batch)
    worker = Thread(target=manager.execute_initial, args=(batch.batch_id, 1))
    worker.start()
    assert started.wait(2)
    running = manager.get(batch.batch_id)
    assert running is not None
    child = running.children[0]
    assert child.cancel_action is not None
    updated, accepted = manager.cancel(
        batch.batch_id, child.child_id or "", child.cancel_action.nonce, "remaining"
    )
    assert accepted is True
    assert updated.children[0].status == "cancelling"
    release.set()
    worker.join(2)
    settled = manager.get(batch.batch_id)
    assert settled is not None
    assert settled.children[0].status == "cancelled"
    manager.close()


@pytest.mark.parametrize(
    ("action_result", "expected_status", "expected_result"),
    [
        (RunStatus.LOCAL_ONLY, "cancelled", RunStatus.CANCELLED.value),
        (RunStatus.DRAFT_SAVED, "completed", RunStatus.DRAFT_SAVED.value),
    ],
)
def test_running_child_action_cannot_overwrite_an_accepted_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action_result: RunStatus,
    expected_status: str,
    expected_result: str,
) -> None:
    entered = Event()
    release = Event()

    def runner(request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            request.run_id or "RUN-fixture",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state.json",
            tmp_path / "run.jsonl",
            (),
            "ready for continuation",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(runner)
    )

    def blocked_action(
        _context: ManualRunContext,
        _dependencies: ManualRunDependencies,
        child: ManualRunView,
        _kind: str,
    ) -> ManualRunView:
        entered.set()
        assert release.wait(timeout=2)
        return replace(
            child,
            status="completed",
            result_status=action_result.value,
            message=(
                "네이버 임시저장 완료"
                if action_result is RunStatus.DRAFT_SAVED
                else "ready for continuation"
            ),
            next_action=None,
            active_action=None,
        )

    try:
        batch = new_batch(
            parse_manual_run_payload(
                {"keyword": "취소 정착 경쟁", "as_of_date": "2026-09-01"}
            )
        )
        ManualBatchStore(tmp_path).save(batch)
        manager.execute_initial(batch.batch_id, 1)
        ready = manager.get(batch.batch_id)
        assert ready is not None
        child = ready.children[0]
        assert child.child_id is not None
        assert child.next_action is not None
        assert child.cancel_action is not None
        monkeypatch.setattr(
            "tools.dashboard_manual_run.execute_child_action", blocked_action
        )
        _ = manager.submit_action(
            batch.batch_id,
            child.child_id,
            "external",
            child.next_action.nonce,
        )
        assert entered.wait(timeout=1)
        running = manager.get(batch.batch_id)
        assert running is not None
        active_child = running.children[0]
        assert active_child.cancel_action is not None
        cancelling, accepted = manager.cancel(
            batch.batch_id,
            child.child_id,
            active_child.cancel_action.nonce,
            "remaining",
        )
        assert accepted is True
        assert cancelling.children[0].status == "cancelling"
    finally:
        release.set()
        manager.close()

    settled = manager.get(batch.batch_id)
    assert settled is not None
    assert settled.children[0].status == expected_status
    assert settled.children[0].result_status == expected_result
    assert settled.children[0].cancellation is not None
    assert settled.children[0].cancellation.completed_at is not None


def test_uncertain_naver_save_failure_keeps_reconciliation_state_during_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def runner(request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            request.run_id or "RUN-fixture",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state.json",
            tmp_path / "run.jsonl",
            (),
            "ready for continuation",
        )

    original_batch = new_batch(
        parse_manual_run_payload(
            {"keyword": "불확실한 저장 취소", "as_of_date": "2026-09-01"}
        )
    )
    original = original_batch.children[0]
    assert original.run_id is not None
    assert original.child_id is not None
    assert original.cancel_action is not None
    pending_child = replace(
        original,
        status="completed",
        result_status=RunStatus.AWAITING_USER_CONFIRMATION.value,
        next_action=ManualActionView("confirm", "confirmation-fixture"),
        cancel_action=replace(original.cancel_action, scope="remaining"),
    )
    pending = replace(original_batch, children=(pending_child,))
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(runner)
    )
    ManualBatchStore(tmp_path).save(pending)

    entered = Event()
    release = Event()

    def uncertain_action(
        _context: ManualRunContext,
        _dependencies: ManualRunDependencies,
        child: ManualRunView,
        _kind: str,
    ) -> ManualRunView:
        entered.set()
        assert release.wait(timeout=2)
        assert child.run_id is not None
        state_path, _, _ = state_paths(tmp_path, child.run_id)
        atomic_write_json(state_path, {"naver_save_outcome_uncertain": True})
        return replace(
            child,
            status="failed",
            result_status=RunStatus.FAILED.value,
            message="네이버 임시저장 결과가 불확실합니다. 수동 대조 필요",
            retryable=False,
            next_action=None,
            active_action=None,
        )

    try:
        monkeypatch.setattr(
            "tools.dashboard_manual_run.execute_child_action", uncertain_action
        )
        _ = manager.submit_action(
            pending.batch_id,
            original.child_id,
            "confirm",
            "confirmation-fixture",
        )
        assert entered.wait(timeout=1)
        running = manager.get(pending.batch_id)
        assert running is not None
        child = running.children[0]
        assert child.cancel_action is not None
        _, accepted = manager.cancel(
            pending.batch_id,
            original.child_id,
            child.cancel_action.nonce,
            "remaining",
        )
        assert accepted is True
    finally:
        release.set()
        manager.close()

    settled = manager.get(pending.batch_id)
    assert settled is not None
    assert settled.children[0].status == "failed"
    assert settled.children[0].result_status == RunStatus.FAILED.value
    assert settled.children[0].cancellation is not None
    assert settled.children[0].cancellation.completed_at is not None
    assert settled.children[0].next_action is None
