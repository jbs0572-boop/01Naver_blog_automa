from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import PIPELINE_VERSION, ContractError, JSONMap, JSONValue
from tools.log_contract import validate_log

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_historical_logs_remain_readable() -> None:
    assert validate_log(PROJECT_ROOT / "runs" / "beta-2026-08-26-energy-day.jsonl") == 8
    assert (
        validate_log(PROJECT_ROOT / "runs" / "beta-gate0-2026-08-26-9topics.jsonl")
        == 66
    )


def test_legacy_status_aliases_remain_supported(tmp_path: Path) -> None:
    events = [
        {"event_type": "stage", "stage": "researcher", "status": "success"},
        {"event_type": "stage", "stage": "writer", "status": "completed"},
        {"event_type": "stage", "stage": "image-maker", "status": "not-run"},
    ]
    path = tmp_path / "legacy.jsonl"
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    assert validate_log(path) == 3


def test_malformed_jsonl_and_unknown_event_are_rejected(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.jsonl"
    _ = malformed.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid JSON"):
        _ = validate_log(malformed)

    unknown = tmp_path / "unknown.jsonl"
    _ = unknown.write_text(
        json.dumps({"event_type": "unknown"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(ContractError, match="invalid event_type"):
        _ = validate_log(unknown)


def test_optimized_event_requires_timezone(tmp_path: Path) -> None:
    event = {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "batch_id": "BATCH-log",
        "run_id": "RUN-log",
        "topic_id": "TOPIC-log",
        "stage": "researcher",
        "started_at": "2026-08-27T00:00:00",
        "ended_at": "2026-08-27T00:01:00+00:00",
        "status": "passed",
        "attempt": 1,
    }
    path = tmp_path / "optimized.jsonl"
    _ = path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ContractError, match="schema"):
        _ = validate_log(path)


def _telemetry_event(
    stage: str,
    started_at: str,
    ended_at: str,
    *,
    depends_on: list[str] | None = None,
    duration_ms: JSONValue = 1000.0,
) -> JSONMap:
    event: JSONMap = {
        "event_type": "stage",
        "pipeline_version": PIPELINE_VERSION,
        "telemetry_version": 2,
        "batch_id": "BATCH-log",
        "run_id": "RUN-log",
        "topic_id": "TOPIC-log",
        "stage": stage,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
        "status": "passed",
        "attempt": 1,
    }
    if depends_on is not None:
        dependency_values: list[JSONValue] = [dependency for dependency in depends_on]
        event["depends_on"] = dependency_values
    return event


def test_telemetry_rejects_negative_wall_interval(tmp_path: Path) -> None:
    # Given
    event = _telemetry_event(
        "researcher",
        "2026-08-27T00:02:00+00:00",
        "2026-08-27T00:01:00+00:00",
    )
    path = tmp_path / "negative.jsonl"
    _ = path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    # When / Then
    with pytest.raises(ContractError, match="chronology"):
        _ = validate_log(path)


def test_telemetry_rejects_dependency_overlap(tmp_path: Path) -> None:
    # Given
    events = [
        _telemetry_event(
            "content-assembler",
            "2026-08-27T00:00:00+00:00",
            "2026-08-27T00:02:00+00:00",
        ),
        _telemetry_event(
            "notion-rider",
            "2026-08-27T00:01:00+00:00",
            "2026-08-27T00:03:00+00:00",
            depends_on=["content-assembler"],
        ),
    ]
    path = tmp_path / "overlap.jsonl"
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    # When / Then
    with pytest.raises(ContractError, match="dependency chronology"):
        _ = validate_log(path)


def test_legacy_optimized_events_remain_readable_without_telemetry_v2(
    tmp_path: Path,
) -> None:
    # Given
    events = [
        {
            key: value
            for key, value in _telemetry_event(
                "image-maker",
                "2026-08-27T00:00:00+00:00",
                "2026-08-27T00:02:00+00:00",
            ).items()
            if key not in {"telemetry_version", "duration_ms"}
        },
        {
            key: value
            for key, value in _telemetry_event(
                "content-assembler",
                "2026-08-27T00:01:00+00:00",
                "2026-08-27T00:03:00+00:00",
            ).items()
            if key not in {"telemetry_version", "duration_ms"}
        },
    ]
    path = tmp_path / "legacy-optimized.jsonl"
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    # When / Then
    assert validate_log(path) == 2


@pytest.mark.parametrize(
    "duration_ms",
    (True, float("nan"), float("inf"), float("-inf"), -1.0),
)
def test_telemetry_rejects_malformed_duration(
    tmp_path: Path, duration_ms: JSONValue
) -> None:
    # Given
    event = _telemetry_event(
        "researcher",
        "2026-08-27T00:00:00+00:00",
        "2026-08-27T00:01:00+00:00",
        duration_ms=duration_ms,
    )
    path = tmp_path / "malformed-duration.jsonl"
    _ = path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    # When / Then
    with pytest.raises(ContractError, match="duration_ms"):
        _ = validate_log(path)


def test_telemetry_rejects_same_stage_overlap(tmp_path: Path) -> None:
    # Given
    events = [
        _telemetry_event(
            "researcher",
            "2026-08-27T00:00:00+00:00",
            "2026-08-27T00:02:00+00:00",
        ),
        _telemetry_event(
            "researcher",
            "2026-08-27T00:01:00+00:00",
            "2026-08-27T00:03:00+00:00",
        ),
    ]
    path = tmp_path / "same-stage-overlap.jsonl"
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    # When / Then
    with pytest.raises(ContractError, match="chronology"):
        _ = validate_log(path)


def test_telemetry_does_not_compare_stage_intervals_across_runs(tmp_path: Path) -> None:
    # Given
    first = _telemetry_event(
        "researcher",
        "2026-08-27T00:00:00+00:00",
        "2026-08-27T00:02:00+00:00",
    )
    second = _telemetry_event(
        "researcher",
        "2026-08-27T00:01:00+00:00",
        "2026-08-27T00:03:00+00:00",
    )
    second["run_id"] = "RUN-other"
    path = tmp_path / "different-runs.jsonl"
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in (first, second)),
        encoding="utf-8",
    )

    # When / Then
    assert validate_log(path) == 2


def test_telemetry_allows_absent_prior_dependency(tmp_path: Path) -> None:
    # Given
    event = _telemetry_event(
        "writer",
        "2026-08-27T00:00:00+00:00",
        "2026-08-27T00:01:00+00:00",
        depends_on=["researcher"],
    )
    path = tmp_path / "absent-dependency.jsonl"
    _ = path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    # When / Then
    assert validate_log(path) == 1


@pytest.mark.parametrize("telemetry_version", (True, "2", 3, None))
def test_telemetry_rejects_present_unsupported_version(
    tmp_path: Path, telemetry_version: JSONValue
) -> None:
    event = _telemetry_event(
        "researcher",
        "2026-08-27T00:00:00+00:00",
        "2026-08-27T00:01:00+00:00",
    )
    event["telemetry_version"] = telemetry_version
    path = tmp_path / "unsupported-version.jsonl"
    _ = path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ContractError, match="unsupported telemetry_version"):
        _ = validate_log(path)


def test_unsupported_telemetry_version_is_rejected_before_v2_field_validation(
    tmp_path: Path,
) -> None:
    event = _telemetry_event("researcher", "not-a-timestamp", "also-not-a-timestamp")
    event["telemetry_version"] = 3
    event["duration_ms"] = -1.0
    path = tmp_path / "unsupported-version-invalid-fields.jsonl"
    _ = path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ContractError, match="unsupported telemetry_version"):
        _ = validate_log(path)
