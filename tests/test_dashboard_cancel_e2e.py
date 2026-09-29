from __future__ import annotations

from pathlib import Path
from threading import Event

from tools.dashboard_manual_models import ManualRunContext, ManualRunDependencies
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.dashboard_manual_run import ManualRunManager
from tools.runner_types import RunnerRequest, RunnerResult, RunStatus


def test_three_item_queue_cancel_does_not_skip_following_work(tmp_path: Path) -> None:
    started = Event()
    release = Event()
    calls: list[str] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        run_id = request.run_id or "missing"
        calls.append(run_id)
        if len(calls) == 1:
            started.set()
            assert release.wait(2)
        result = RunnerResult(run_id, RunStatus.LOCAL_ONLY, tmp_path / "state", tmp_path / "log", (), "done")
        return result

    manager = ManualRunManager(ManualRunContext(tmp_path, False), ManualRunDependencies(runner))
    try:
        first = manager.start(parse_manual_run_payload({"keyword": "A 실행", "as_of_date": "2026-09-10"}))
        second = manager.start(parse_manual_run_payload({"keyword": "B 취소", "as_of_date": "2026-09-10"}))
        third = manager.start(parse_manual_run_payload({"keyword": "C 계속", "as_of_date": "2026-09-10"}))
        assert started.wait(2)
        second_state = manager.get(second.batch_id)
        assert second_state is not None
        child = second_state.children[0]
        assert child.cancel_action is not None
        cancelled, accepted = manager.cancel(
            second.batch_id,
            child.child_id or "",
            child.cancel_action.nonce,
            "queued_only",
        )
        assert accepted is False
        assert cancelled.children[0].status == "cancelled"
        release.set()
        manager.close()
        assert calls == [first.children[0].run_id, third.children[0].run_id]
        second_state = manager.get(second.batch_id)
        third_state = manager.get(third.batch_id)
        assert second_state is not None and third_state is not None
        assert second_state.children[0].status == "cancelled"
        assert third_state.children[0].result_status == RunStatus.LOCAL_ONLY.value
    finally:
        manager.close()
