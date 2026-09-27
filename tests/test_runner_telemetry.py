from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.automation_runner import run_job
from tools.codex_stage_error import StageExecutionError, StageFailureType
from tools.contract_types import ContractError, JSONMap
from tools.log_contract import read_events
from tools.runner_loop import execute_run
from tools.runner_state import input_fingerprint
from tools.runner_types import (
    JobName,
    RunExecutionContext,
    RunnerRequest,
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
    TopicSelectionContext,
)
from tools.weekly_report import write_weekly_report


class RetryOnceExecutor:
    def __init__(self) -> None:
        self._topic_calls: int = 0

    def execute(self, context: StageExecutionContext) -> StageResult:
        if context.stage == "topic-selector":
            self._topic_calls += 1
            if self._topic_calls == 1:
                raise OSError("temporary executor failure")
        return StageResult(RunStatus.PASSED, StageExecution.PRODUCED)


class RepairOnceExecutor:
    def __init__(self) -> None:
        self._assembler_calls: int = 0

    def execute(self, context: StageExecutionContext) -> StageResult:
        if context.stage == "content-assembler":
            self._assembler_calls += 1
            if self._assembler_calls == 1:
                raise ContractError("repair required")
        return StageResult(RunStatus.PASSED, StageExecution.PRODUCED)


class FailTwiceExecutor:
    def __init__(self) -> None:
        self.calls: int = 0

    def execute(self, context: StageExecutionContext) -> StageResult:
        if context.stage == "topic-selector":
            self.calls += 1
            if self.calls <= 2:
                raise OSError("temporary executor failure")
        return StageResult(RunStatus.PASSED, StageExecution.PRODUCED)


class ClassifiedRetryExecutor:
    def __init__(self) -> None:
        self.calls: int = 0

    def execute(self, context: StageExecutionContext) -> StageResult:
        if context.stage == "topic-selector":
            self.calls += 1
            if self.calls == 1:
                raise StageExecutionError(
                    context.stage,
                    "temporary process start failure",
                    StageFailureType.TEMPORARY_IO.value,
                    True,
                    "retry writer",
                )
        return StageResult(RunStatus.PASSED, StageExecution.PRODUCED)


def _root(tmp_path: Path, keyword: str = "telemetry-topic") -> Path:
    final_dir = tmp_path / "final"
    asset_dir = tmp_path / "assets" / keyword
    final_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "body.png").write_bytes(b"body")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
    _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
    _ = (final_dir / f"{keyword}.md").write_text(
        f"![body](../assets/{keyword}/body.png)\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-layout.md").write_text(
        "# layout\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-copy.md").write_text(
        "# copy\n", encoding="utf-8"
    )
    _ = (final_dir / f"{keyword}-naver-input.md").write_text(
        "# input\n", encoding="utf-8"
    )
    return tmp_path


def _request(
    root: Path,
    executor: RetryOnceExecutor | RepairOnceExecutor | FailTwiceExecutor | ClassifiedRetryExecutor | None = None,
) -> RunnerRequest:
    return RunnerRequest(
        root=root,
        job="daily-generate",
        keyword="telemetry-topic",
        run_id="RUN-telemetry",
        executor=executor,
        selection_context=TopicSelectionContext("", "", "", "2026-09-07"),
    )


def _install_deterministic_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    wall = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    monotonic = 0

    def advancing_now(_request: RunnerRequest) -> datetime:
        nonlocal wall
        value = wall
        wall += timedelta(seconds=1)
        return value

    def advancing_monotonic_ns(_request: RunnerRequest) -> int:
        nonlocal monotonic
        value = monotonic
        monotonic += 250_000_000
        return value

    def skip_sleep(_seconds: float) -> None:
        return

    monkeypatch.setattr("tools.runner_loop.now", advancing_now)
    monkeypatch.setattr(
        "tools.runner_loop.monotonic_ns", advancing_monotonic_ns, raising=False
    )
    monkeypatch.setattr("tools.runner_stages.time.sleep", skip_sleep)


def _string_field(event: JSONMap, key: str) -> str:
    value = event.get(key)
    assert isinstance(value, str)
    return value


def _duration_ms(event: JSONMap) -> float:
    value = event.get("duration_ms")
    assert isinstance(value, int | float) and not isinstance(value, bool)
    return float(value)


def _batch_slot(event: JSONMap) -> int:
    value = event.get("batch_slot")
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


def test_stage_events_record_independent_monotonic_intervals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _install_deterministic_timing(monkeypatch)
    request = _request(_root(tmp_path))

    # When
    result = run_job(request)

    # Then
    events = read_events(result.log_path)
    starts = [_string_field(event, "started_at") for event in events]
    assert len(set(starts)) == len(starts)
    assert all(event["telemetry_version"] == 2 for event in events)
    assert all(_duration_ms(event) == 250.0 for event in events)


def test_transient_retry_records_each_attempt_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _install_deterministic_timing(monkeypatch)
    request = _request(_root(tmp_path), RetryOnceExecutor())

    # When
    result = run_job(request)

    # Then
    attempts = [
        event
        for event in read_events(result.log_path)
        if event["stage"] == "topic-selector"
    ]
    assert [event["attempt"] for event in attempts] == [1, 2]
    assert [event["status"] for event in attempts] == ["failed", "passed"]
    assert _string_field(attempts[1], "started_at") >= _string_field(
        attempts[0], "ended_at"
    )


def test_classified_temporary_io_retries_once_with_runner_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_deterministic_timing(monkeypatch)
    sleeps: list[float] = []
    monkeypatch.setattr("tools.runner_stages.time.sleep", sleeps.append)
    executor = ClassifiedRetryExecutor()

    result = run_job(_request(_root(tmp_path), executor))

    attempts = [
        event
        for event in read_events(result.log_path)
        if event["stage"] == "topic-selector"
    ]
    assert executor.calls == 2
    assert sleeps == [1.0]
    assert [event["status"] for event in attempts] == ["failed", "passed"]
    assert attempts[0]["error_type"] == "temporary_io"


def test_manual_resume_does_not_reuse_stage_attempt_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_deterministic_timing(monkeypatch)
    executor = FailTwiceExecutor()
    request = _request(_root(tmp_path), executor)

    state_path = tmp_path / ".automation/state/RUN-telemetry.json"
    log_path = tmp_path / ".automation/logs/RUN-telemetry.jsonl"
    context = RunExecutionContext(
        request,
        JobName.DAILY_GENERATE,
        "RUN-telemetry",
        state_path,
        log_path,
        input_fingerprint(request),
        True,
    )
    first = execute_run(context)
    assert first.status is RunStatus.FAILED
    second = execute_run(context)

    attempts = [
        event["attempt"]
        for event in read_events(second.log_path)
        if event["stage"] == "topic-selector"
    ]
    assert attempts == [1, 2, 3]


def test_q1_repair_records_each_attempt_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _install_deterministic_timing(monkeypatch)
    request = _request(_root(tmp_path), RepairOnceExecutor())

    # When
    result = run_job(request)

    # Then
    attempts = [
        event
        for event in read_events(result.log_path)
        if event["stage"] == "content-assembler"
    ]
    assert [event["attempt"] for event in attempts] == [1, 2]
    assert len({_string_field(event, "started_at") for event in attempts}) == 2
    assert all(_duration_ms(event) == 250.0 for event in attempts)


def test_weekly_report_prefers_monotonic_duration(tmp_path: Path) -> None:
    # Given
    log_dir = tmp_path / ".automation" / "logs"
    log_dir.mkdir(parents=True)
    event = {
        "event_type": "stage",
        "pipeline_version": "workflow-optimized-v1",
        "telemetry_version": 2,
        "batch_id": "BATCH-telemetry",
        "run_id": "RUN-telemetry",
        "topic_id": "TOPIC-telemetry",
        "stage": "researcher",
        "started_at": "2026-08-27T12:00:00+00:00",
        "ended_at": "2026-08-27T12:01:00+00:00",
        "duration_ms": 1250.0,
        "status": "passed",
        "attempt": 1,
    }
    _ = (log_dir / "RUN-telemetry.jsonl").write_text(
        json.dumps(event) + "\n", encoding="utf-8"
    )

    # When
    report = write_weekly_report(tmp_path, datetime(2026, 8, 27, 12, 0, tzinfo=UTC))

    # Then
    assert "stage_duration_seconds: 1.250" in report.read_text(encoding="utf-8")


def test_weekly_report_uses_wall_duration_for_legacy_event(tmp_path: Path) -> None:
    # Given
    log_dir = tmp_path / ".automation" / "logs"
    log_dir.mkdir(parents=True)
    event = {
        "event_type": "stage",
        "pipeline_version": "workflow-optimized-v1",
        "batch_id": "BATCH-legacy",
        "run_id": "RUN-legacy",
        "topic_id": "TOPIC-legacy",
        "stage": "researcher",
        "started_at": "2026-08-27T12:00:00+00:00",
        "ended_at": "2026-08-27T12:01:00+00:00",
        "status": "passed",
        "attempt": 1,
    }
    _ = (log_dir / "RUN-legacy.jsonl").write_text(
        json.dumps(event) + "\n", encoding="utf-8"
    )

    # When
    report = write_weekly_report(tmp_path, datetime(2026, 8, 27, 12, 0, tzinfo=UTC))

    # Then
    assert "stage_duration_seconds: 60.000" in report.read_text(encoding="utf-8")


def test_weekly_report_rejects_invalid_v2_duration(tmp_path: Path) -> None:
    # Given
    log_dir = tmp_path / ".automation" / "logs"
    log_dir.mkdir(parents=True)
    event = {
        "event_type": "stage",
        "pipeline_version": "workflow-optimized-v1",
        "telemetry_version": 2,
        "batch_id": "BATCH-invalid",
        "run_id": "RUN-invalid",
        "topic_id": "TOPIC-invalid",
        "stage": "researcher",
        "started_at": "2026-08-27T12:00:00+00:00",
        "ended_at": "2026-08-27T12:01:00+00:00",
        "duration_ms": -1.0,
        "status": "passed",
        "attempt": 1,
    }
    _ = (log_dir / "RUN-invalid.jsonl").write_text(
        json.dumps(event) + "\n", encoding="utf-8"
    )

    # When / Then
    with pytest.raises(ContractError, match="duration_ms"):
        _ = write_weekly_report(tmp_path, datetime(2026, 8, 27, 12, 0, tzinfo=UTC))


def test_weekly_report_counts_retry_events_without_triangular_overcount(
    tmp_path: Path,
) -> None:
    # Given
    log_dir = tmp_path / ".automation" / "logs"
    log_dir.mkdir(parents=True)
    events = [
        {
            "event_type": "stage",
            "pipeline_version": "workflow-optimized-v1",
            "telemetry_version": 2,
            "batch_id": "BATCH-retries",
            "run_id": "RUN-retries",
            "topic_id": "TOPIC-retries",
            "stage": "content-assembler",
            "started_at": f"2026-08-27T12:00:0{attempt - 1}+00:00",
            "ended_at": f"2026-08-27T12:00:0{attempt}+00:00",
            "duration_ms": 1000.0,
            "status": "failed" if attempt < 3 else "passed",
            "attempt": attempt,
        }
        for attempt in (1, 2, 3)
    ]
    _ = (log_dir / "RUN-retries.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    # When
    report = write_weekly_report(tmp_path, datetime(2026, 8, 27, 12, 0, tzinfo=UTC))

    # Then
    assert "retries: 2" in report.read_text(encoding="utf-8")


def test_end_to_end_runner_telemetry_drives_weekly_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _install_deterministic_timing(monkeypatch)
    result = run_job(_request(_root(tmp_path)))
    events = read_events(result.log_path)
    expected_seconds = sum(_duration_ms(event) for event in events) / 1000.0

    # When
    report = write_weekly_report(tmp_path, datetime(2026, 8, 27, 12, 0, tzinfo=UTC))

    # Then
    assert f"stage_duration_seconds: {expected_seconds:.3f}" in report.read_text(
        encoding="utf-8"
    )


def test_stage_event_uses_context_batch_identity_and_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _install_deterministic_timing(monkeypatch)
    context = TopicSelectionContext(
        "", "", "", "2026-09-07", batch_id="BATCH-context", batch_slot=2
    )
    request = RunnerRequest(
        tmp_path,
        "daily-generate",
        keyword="telemetry-topic",
        run_id="RUN-context",
        selection_context=context,
    )

    # When
    result = run_job(request)

    # Then
    events = read_events(result.log_path)
    assert {_string_field(event, "batch_id") for event in events} == {"BATCH-context"}
    assert {_batch_slot(event) for event in events} == {2}


def test_unbatched_runs_use_run_scoped_batch_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _install_deterministic_timing(monkeypatch)
    request = _request(_root(tmp_path))

    # When
    result = run_job(request)

    # Then
    events = read_events(result.log_path)
    assert {_string_field(event, "batch_id") for event in events} == {
        "BATCH-RUN-telemetry"
    }
    assert all("batch_slot" not in event for event in events)
