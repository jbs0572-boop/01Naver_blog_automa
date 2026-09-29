from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

import pytest

from tools.dashboard_health import DashboardHealth
from tools.dashboard_notifications import DashboardNotifications
from tools.dashboard_schedule import DailySchedule
from tools.dashboard_snapshot_cache import RunSnapshotCache


def _write_run(root: Path, run_id: str, updated_at: str) -> None:
    log = root / "runs" / f"{run_id}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    _ = log.write_text(
        json.dumps(
            {
                "event_type": "stage",
                "run_id": run_id,
                "topic_id": "TOPIC-test",
                "keyword": run_id,
                "stage": "content-assembler",
                "status": "passed",
                "ended_at": updated_at,
                "quality": {"q1": "passed"},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_run_cache_pages_without_reparsing_unchanged_logs(tmp_path: Path) -> None:
    # Given
    for index in range(3):
        _write_run(tmp_path, f"RUN-{index}", f"2026-09-0{index + 1}T00:00:00+09:00")
    cache = RunSnapshotCache(tmp_path)

    # When
    first = cache.query(limit=2)
    second = cache.query(limit=2, cursor=str(first["next_cursor"]))
    warm = cache.query(limit=2)

    # Then
    first_items = first["items"]
    second_items = second["items"]
    assert isinstance(first_items, list) and all(isinstance(item, dict) for item in first_items)
    assert isinstance(second_items, list) and all(isinstance(item, dict) for item in second_items)
    assert [item["run_id"] for item in first_items if isinstance(item, dict)] == ["RUN-2", "RUN-1"]
    assert [item["run_id"] for item in second_items if isinstance(item, dict)] == ["RUN-0"]
    assert first["global_summary"] == {"total": 3, "by_status": {"passed": 3}}
    assert warm["parse_count"] == first["parse_count"]


def test_run_cache_refreshes_derived_values_when_state_payload_changes_at_same_timestamp(
    tmp_path: Path,
) -> None:
    # Given: an operator rewrites the status while preserving its business and mtime timestamps.
    _write_run(tmp_path, "RUN-stale", "2026-09-01T00:00:00+09:00")
    state = tmp_path / ".automation" / "state" / "RUN-stale.json"
    state.parent.mkdir(parents=True)
    _ = state.write_text(
        json.dumps({"run_id": "RUN-stale", "status": "passed", "updated_at": "2026-09-01T00:00:00+09:00"}),
        encoding="utf-8",
    )
    cache = RunSnapshotCache(tmp_path)
    first = cache.query()
    original = state.stat()

    # When
    _ = state.write_text(
        json.dumps({"run_id": "RUN-stale", "status": "failed", "updated_at": "2026-09-01T00:00:00+09:00"}),
        encoding="utf-8",
    )
    os.utime(state, ns=(original.st_atime_ns, original.st_mtime_ns))
    refreshed = cache.query()

    # Then: a changed state must not reuse the previous derived result.
    first_items = first["items"]
    refreshed_items = refreshed["items"]
    assert isinstance(first_items, list) and isinstance(first_items[0], dict)
    assert isinstance(refreshed_items, list) and isinstance(refreshed_items[0], dict)
    assert first_items[0]["status"] == "passed"
    assert refreshed_items[0]["status"] == "failed"
    assert refreshed["global_summary"] == {"total": 1, "by_status": {"failed": 1}}


def test_run_cache_refreshes_usage_when_only_token_log_changes(tmp_path: Path) -> None:
    # Given
    _write_run(tmp_path, "RUN-usage", "2026-09-01T00:00:00+09:00")
    usage_log = tmp_path / ".automation" / "work" / "RUN-usage" / "writer" / "codex-attempt-1.jsonl"
    usage_log.parent.mkdir(parents=True)
    _ = usage_log.write_text(
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 0}}) + "\n",
        encoding="utf-8",
    )
    cache = RunSnapshotCache(tmp_path)
    first = cache.query()

    # When
    original = usage_log.stat()
    _ = usage_log.write_text(
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 4, "cached_input_tokens": 0}}) + "\n",
        encoding="utf-8",
    )
    os.utime(usage_log, ns=(original.st_atime_ns, original.st_mtime_ns))
    refreshed = cache.query()

    # Then
    assert first["global_usage"] == {
        "input_tokens": 10,
        "output_tokens": 2,
        "cached_input_tokens": 0,
        "total_tokens": 12,
        "recorded_runs": 1,
    }
    assert refreshed["global_usage"] == {
        "input_tokens": 20,
        "output_tokens": 4,
        "cached_input_tokens": 0,
        "total_tokens": 24,
        "recorded_runs": 1,
    }


def test_missed_schedule_retry_is_idempotent_and_preserves_date(tmp_path: Path) -> None:
    # Given
    schedule = DailySchedule(tmp_path)
    started = datetime.fromisoformat("2026-09-10T07:00:00+09:00")
    _ = schedule.save({"times": ["08:00"], "enabled": True}, started)
    schedule.tick(
        datetime.fromisoformat("2026-09-10T09:00:00+09:00"),
        lambda: False,
        lambda _day, _scheduled_at, _occurrence_id, _model_config: "never",
    )
    history = schedule.data["history"]
    assert isinstance(history, list) and isinstance(history[0], dict)
    occurrence = history[0]
    calls: list[str] = []

    # When
    first = schedule.retry(
        str(occurrence["occurrence_id"]),
        "nonce-1",
        lambda day, _scheduled_at, _occurrence_id, _model_config: calls.append(day)
        or "BATCH-one",
    )
    second = schedule.retry(
        str(occurrence["occurrence_id"]),
        "nonce-1",
        lambda _day, _scheduled_at, _occurrence_id, _model_config: "BATCH-two",
    )

    # Then
    assert calls == ["2026-09-10"]
    assert first == second
    assert occurrence["status"] == "missed"


def test_notifications_deduplicate_and_persist_read_state(tmp_path: Path) -> None:
    # Given
    notifications = DashboardNotifications(tmp_path)

    # When
    _ = notifications.record("RUN-one", "failed", "attempt-1", "실행 실패")
    _ = notifications.record("RUN-one", "failed", "attempt-1", "실행 실패")
    items = notifications.view()["items"]
    assert isinstance(items, list) and isinstance(items[0], dict)
    notice_id = str(items[0]["id"])
    _ = notifications.mark_read(notice_id)

    # Then
    restored = DashboardNotifications(tmp_path).view()
    restored_items = restored["items"]
    assert isinstance(restored_items, list) and len(restored_items) == 1
    assert restored["unread_count"] == 0


def test_health_check_is_read_only_and_reports_revision(tmp_path: Path) -> None:
    # Given
    calls: list[Path] = []
    health = DashboardHealth(tmp_path, lambda root: calls.append(root) or (True, None))

    # When
    accepted = health.start()
    assert health.wait(timeout=1)
    result = health.view()

    # Then
    assert accepted["status"] in {"checking", "ready"}
    assert result["status"] == "ready"
    assert result["revision"] == 1
    assert calls == [tmp_path]


def test_health_probe_exception_completes_with_failed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a health probe that raises unexpectedly in its worker thread.
    expected_error = RuntimeError("probe failed")
    errors: list[BaseException | None] = []

    def fail_probe(_root: Path) -> tuple[bool, str | None]:
        raise expected_error

    def capture_thread_error(args: threading.ExceptHookArgs) -> None:
        errors.append(args.exc_value)

    monkeypatch.setattr(threading, "excepthook", capture_thread_error)
    health = DashboardHealth(tmp_path, fail_probe)

    # When: the read-only health check runs.
    _ = health.start()
    completed = health.wait(timeout=1)
    result = health.view()

    # Then: clients see a completed failure instead of an endless checking state.
    assert completed is True
    assert result["status"] == "failed"
    assert result["error_code"] == "health_probe_exception"
    assert result["revision"] == 1
    assert errors == [expected_error]
