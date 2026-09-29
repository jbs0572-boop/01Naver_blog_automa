from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.contract_types import JSONMap, JSONValue
from tools.dashboard_data import RunView
from tools.dashboard_manual_batch import new_batch
from tools.dashboard_manual_models import (
    ManualActiveActionView,
    ManualRunContext,
    ManualRunDependencies,
)
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.dashboard_manual_run import ManualRunManager
from tools.dashboard_manual_store import ManualBatchStore
from tools.dashboard_snapshot_cache import RunSnapshotCache
from tools.dashboard_tasks import DashboardTasks, effective_status
from tools.runner_types import RunnerResult, RunStatus


def _items(value: JSONValue) -> list[JSONMap]:
    assert isinstance(value, list)
    items = [item for item in value if isinstance(item, dict)]
    assert len(items) == len(value)
    return items


def _runner(tmp_path: Path) -> RunnerResult:
    return RunnerResult(
        "RUN-test",
        RunStatus.LOCAL_ONLY,
        tmp_path / "state",
        tmp_path / "log",
        (),
        "not invoked",
    )


def test_tasks_snapshot_includes_all_queued_children_and_stable_queue_order(tmp_path: Path) -> None:
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(lambda _request: _runner(tmp_path)),
    )
    try:
        batches = [new_batch(parse_manual_run_payload({"keyword": f"큐 {index}", "as_of_date": "2026-09-10"})) for index in range(25)]
        for batch in batches:
            ManualBatchStore(tmp_path).save(batch)
        tasks = DashboardTasks(manager, RunSnapshotCache(tmp_path))
        result = tasks.query(limit=100)
        items = _items(result["items"])
        assert len(items) == 25
        assert [item["queue_position"] for item in items[:3]] == [1, 2, 3]
        filtered = tasks.query(q="큐 24", limit=20)
        filtered_items = _items(filtered["items"])
        assert len(filtered_items) == 1
        assert filtered_items[0]["queue_position"] == 25
        summary = result["global_summary"]
        assert isinstance(summary, dict)
        by_status = summary["by_status"]
        assert isinstance(by_status, dict)
        assert by_status["queued"] == 25
    finally:
        manager.close()


def test_terminal_run_state_overrides_stale_running_child() -> None:
    batch = new_batch(parse_manual_run_payload({"keyword": "완료 상태", "as_of_date": "2026-09-10"}))
    child = batch.children[0]
    assert child.status == "queued"
    assert effective_status(child, {"status": "failed"}) == "failed"


def test_active_action_status_overrides_preserved_terminal_result() -> None:
    batch = new_batch(parse_manual_run_payload({"keyword": "확인 재개", "as_of_date": "2026-09-10"}))
    child = batch.children[0]
    active = ManualActiveActionView(
        "confirm",
        "OPERATION-confirm",
        "sha256:" + "a" * 64,
        datetime.now(UTC).isoformat(),
        "queued",
    )
    queued = replace(
        child,
        status="queued",
        result_status=RunStatus.AWAITING_USER_CONFIRMATION.value,
        active_action=active,
    )

    assert effective_status(
        queued, {"status": RunStatus.AWAITING_USER_CONFIRMATION.value}
    ) == "queued"

    running = replace(queued, status="running", active_action=None)
    assert effective_status(
        running, {"status": RunStatus.AWAITING_USER_CONFIRMATION.value}
    ) == "running"


def test_date_filters_use_standalone_run_end_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(lambda _request: _runner(tmp_path)),
    )
    cache = RunSnapshotCache(tmp_path)
    standalone = RunView(
        "RUN-standalone",
        "TOPIC-standalone",
        "standalone run",
        None,
        False,
        "failed",
        "2026-09-05T01:00:00+00:00",
        "2026-09-05T01:10:00+00:00",
        "2026-09-05T01:10:00+00:00",
        "runs/RUN-standalone.jsonl",
        "failed",
        "pending",
        None,
        (),
    )
    monkeypatch.setattr(cache, "runs", lambda: (standalone,))
    tasks = DashboardTasks(manager, cache)

    try:
        included = tasks.query(date_to="2026-09-05")
        excluded = tasks.query(date_to="2026-09-04")
        assert included["filtered_total"] == 1
        assert excluded["filtered_total"] == 0
    finally:
        manager.close()


def test_snapshot_revision_ignores_elapsed_duration(tmp_path: Path) -> None:
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(lambda _request: _runner(tmp_path)),
    )
    try:
        tasks = DashboardTasks(manager, RunSnapshotCache(tmp_path))
        left: list[JSONMap] = [{"task_id": "one", "duration_seconds": 1}]
        right: list[JSONMap] = [{"task_id": "one", "duration_seconds": 99}]
        assert tasks.revision_for(left) == tasks.revision_for(right)
    finally:
        manager.close()
