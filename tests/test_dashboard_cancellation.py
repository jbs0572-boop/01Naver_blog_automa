from pathlib import Path
from threading import Event, Thread

from tools.dashboard_manual_batch import new_batch
from tools.dashboard_manual_models import ManualRunContext, ManualRunDependencies
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.dashboard_manual_run import ManualRunManager
from tools.dashboard_manual_store import ManualBatchStore
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
