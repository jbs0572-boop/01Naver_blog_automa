from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.dashboard_schedule import KST, DailySchedule
from tools.model_presets import ModelConfigSnapshot
from tools.runner_types import RunnerRequest, RunnerResult, RunStatus
from tools.test_dashboard import (
    DashboardServer,
    DashboardServerConfig,
    DashboardServerDependencies,
)


def test_daily_recurrence_is_persistent_and_deduplicated(tmp_path: Path) -> None:
    scheduler = DailySchedule(tmp_path)
    _ = scheduler.save({"times": ["08:00", "12:00", "18:00"], "enabled": True}, datetime.fromisoformat("2026-09-10T07:00:00+09:00"))
    calls: list[str] = []

    def launch(
        day: str,
        _scheduled_at: str,
        _occurrence_id: str,
        _model_config: ModelConfigSnapshot,
    ) -> str:
        calls.append(day)
        return f"BATCH-{len(calls)}"

    for stamp in ["2026-09-10T08:00:00+09:00", "2026-09-10T12:00:00+09:00", "2026-09-10T18:00:00+09:00", "2026-09-11T08:00:00+09:00"]:
        scheduler = DailySchedule(tmp_path)
        now = datetime.fromisoformat(stamp)
        scheduler.tick(now, lambda: False, launch)
        scheduler.tick(now, lambda: False, launch)
    assert calls == ["2026-09-10"] * 3 + ["2026-09-11"]


def test_claimed_occurrence_recovers_after_repeated_process_crashes(
    tmp_path: Path,
) -> None:
    # Given: downstream acceptance is durable and idempotent by occurrence ID.
    scheduler = DailySchedule(tmp_path)
    start = datetime.fromisoformat("2026-09-10T07:59:00+09:00")
    _ = scheduler.save({"times": ["08:00"], "enabled": True}, start)
    batches: dict[str, str] = {}
    attempts: list[str] = []

    def interrupted_launch(
        _day: str,
        _scheduled_at: str,
        occurrence_id: str,
        _model_config: ModelConfigSnapshot,
    ) -> str:
        attempts.append(occurrence_id)
        batch_id = batches.setdefault(occurrence_id, "BATCH-one")
        if len(attempts) < 3:
            raise SystemExit("simulated process crash after durable acceptance")
        return batch_id

    # When: the process dies twice after downstream acceptance, then restarts.
    for stamp in ("08:00", "08:01"):
        with pytest.raises(SystemExit, match="simulated process crash"):
            DailySchedule(tmp_path).tick(
                datetime.fromisoformat(f"2026-09-10T{stamp}:00+09:00"),
                lambda: False,
                interrupted_launch,
            )
    DailySchedule(tmp_path).tick(
        datetime.fromisoformat("2026-09-10T09:00:00+09:00"),
        lambda: False,
        interrupted_launch,
    )

    # Then: replay uses one occurrence identity and resolves to one durable batch.
    restored = DailySchedule(tmp_path)
    history = restored.data["history"]
    assert isinstance(history, list) and len(history) == 1
    occurrence = history[0]
    assert isinstance(occurrence, dict)
    assert attempts == [str(occurrence["occurrence_id"])] * 3
    assert batches == {str(occurrence["occurrence_id"]): "BATCH-one"}
    assert occurrence["status"] == "submitted"
    assert occurrence["batch_id"] == "BATCH-one"


def test_empty_launch_receipt_is_recorded_as_failure(tmp_path: Path) -> None:
    # Given / When: an adapter returns a success-shaped but empty batch receipt.
    scheduler = DailySchedule(tmp_path)
    start = datetime.fromisoformat("2026-09-10T07:59:00+09:00")
    _ = scheduler.save({"times": ["08:00"], "enabled": True}, start)
    scheduler.tick(
        datetime.fromisoformat("2026-09-10T08:00:00+09:00"),
        lambda: False,
        lambda _day, _scheduled_at, _occurrence_id, _model_config: "",
    )

    # Then: the occurrence is not falsely reported as submitted.
    history = scheduler.data["history"]
    assert isinstance(history, list) and isinstance(history[0], dict)
    assert history[0]["status"] == "failed"
    assert "batch_id" not in history[0]



def test_accepted_retry_recovery_relaunches_idempotently_after_restart(
    tmp_path: Path,
) -> None:
    scheduler = DailySchedule(tmp_path)
    configured_at = datetime.fromisoformat("2026-09-10T07:00:00+09:00")
    _ = scheduler.save({"times": ["08:00"], "enabled": True}, configured_at)
    scheduler.tick(
        datetime.fromisoformat("2026-09-10T09:00:00+09:00"),
        lambda: False,
        lambda _day, _at, _occurrence_id, _model_config: "unused",
    )
    history = scheduler.data["history"]
    assert isinstance(history, list) and isinstance(history[0], dict)
    occurrence_id = history[0]["occurrence_id"]
    assert isinstance(occurrence_id, str)

    calls: list[str] = []

    def interrupted_launch(
        _day: str,
        _at: str,
        launch_occurrence_id: str,
        _model_config: ModelConfigSnapshot,
    ) -> str:
        calls.append(launch_occurrence_id)
        raise SystemExit("crash after accepted retry record")

    with pytest.raises(SystemExit, match="accepted retry record"):
        scheduler.retry(occurrence_id, "same-ui-nonce", interrupted_launch)

    restarted = DailySchedule(tmp_path)

    def idempotent_launch(
        _day: str,
        _at: str,
        launch_occurrence_id: str,
        _model_config: ModelConfigSnapshot,
    ) -> str:
        calls.append(launch_occurrence_id)
        return "BATCH-stable"

    recovery = restarted.retry(occurrence_id, "same-ui-nonce", idempotent_launch)
    assert recovery["status"] == "submitted"
    assert recovery["batch_id"] == "BATCH-stable"
    assert calls == [occurrence_id, occurrence_id]

    def must_not_launch(
        _day: str,
        _at: str,
        _occurrence_id: str,
        _model_config: ModelConfigSnapshot,
    ) -> str:
        raise AssertionError("terminal retry result must remain idempotent")

    repeated = DailySchedule(tmp_path).retry(
        occurrence_id, "same-ui-nonce", must_not_launch
    )
    assert repeated == recovery

def test_pause_invalid_time_and_missed_occurrences(tmp_path: Path) -> None:
    scheduler = DailySchedule(tmp_path)
    now = datetime.fromisoformat("2026-09-10T07:00:00+09:00")
    with pytest.raises(ContractError):
        _ = scheduler.save({"times": ["25:00"], "enabled": True}, now)
    _ = scheduler.save({"times": ["08:00"], "enabled": True}, now)
    calls: list[str] = []

    def launch(
        day: str,
        _scheduled_at: str,
        _occurrence_id: str,
        _model_config: ModelConfigSnapshot,
    ) -> str:
        calls.append(day)
        return "BATCH-test"

    scheduler.tick(datetime.fromisoformat("2026-09-10T09:00:00+09:00"), lambda: False, launch)
    assert not calls
    history = scheduler.data["history"]
    assert isinstance(history, list) and isinstance(history[0], dict)
    assert history[0]["at"] == "2026-09-10T08:00:00+09:00"
    assert history[0]["status"] == "missed"
    assert history[0]["reason_code"] == "server_unobserved_or_delayed"
    _ = scheduler.save({"times": ["08:00"], "enabled": False}, now)
    scheduler.tick(datetime.fromisoformat("2026-09-11T08:00:00+09:00"), lambda: False, launch)
    assert not calls


def test_missed_occurrences_are_recorded_after_dashboard_was_offline_overnight(
    tmp_path: Path,
) -> None:
    scheduler = DailySchedule(tmp_path)
    configured_at = datetime.fromisoformat("2026-09-10T07:00:00+09:00")
    _ = scheduler.save({"times": ["08:00", "18:00"], "enabled": True}, configured_at)

    def should_not_launch(
        _day: str,
        _scheduled_at: str,
        _occurrence_id: str,
        _model_config: ModelConfigSnapshot,
    ) -> str:
        raise AssertionError("an overdue occurrence must not launch automatically")

    scheduler.tick(
        datetime.fromisoformat("2026-09-11T09:00:00+09:00"),
        lambda: False,
        should_not_launch,
    )

    history = scheduler.data["history"]
    assert isinstance(history, list)
    missed_at: set[str] = set()
    for item in history:
        if not isinstance(item, dict) or item.get("status") != "missed":
            continue
        at = item.get("at")
        if isinstance(at, str):
            missed_at.add(at)
    assert missed_at == {
        "2026-09-10T08:00:00+09:00",
        "2026-09-10T18:00:00+09:00",
        "2026-09-11T08:00:00+09:00",
    }
    assert scheduler.data["last_observed_date"] == "2026-09-11"


def test_server_timer_starts_one_injected_batch_without_browser(tmp_path: Path) -> None:
    now = datetime.now(KST)
    def runner(request: RunnerRequest) -> RunnerResult:
        return RunnerResult(request.run_id or "RUN-fixture", RunStatus.LOCAL_ONLY, tmp_path / "state.json", tmp_path / "log.jsonl", (), "fixture completed")

    with DashboardServer(DashboardServerConfig(("127.0.0.1", 0), tmp_path), DashboardServerDependencies(runner=runner)) as server:
        _ = server.schedule.save({"times": [now.strftime("%H:%M")], "enabled": True}, now - timedelta(minutes=1))
        server.service_actions()
        server.service_actions()
        server.manual_runs.close()
        batches = server.manual_runs.list(10)
        assert len(batches) == 1
        assert len(batches[0].children) == 1
        assert batches[0].status == "completed"
        assert batches[0].as_of_date == now.date().isoformat()
        progress = server.schedule.progress(tmp_path, now)
        executions = progress["executions"]
        assert isinstance(executions, list) and len(executions) == 1
        execution = executions[0]
        assert isinstance(execution, dict)
        assert execution["time"] == now.strftime("%H:%M")
        assert execution["batch_id"] == batches[0].batch_id
        assert execution["child"] == batches[0].children[0].as_json()
