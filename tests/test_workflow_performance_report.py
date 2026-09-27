import json
from pathlib import Path

from tools.contract_types import JSONMap, JSONValue
from tools.workflow_performance_report import build_report


def _write_run(root: Path, run_id: str, *, status: str, duration: int) -> None:
    log = root / ".automation/logs" / f"{run_id}.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event_type": "stage",
        "run_id": run_id,
        "stage": "writer",
        "status": status,
        "duration_ms": duration,
        "attempt": 1,
        "started_at": "2026-09-01T00:00:00+00:00",
        "ended_at": "2026-09-01T00:00:01+00:00",
        "model": "gpt-test",
        "reasoning_effort": "low",
    }
    _ = log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    state = root / ".automation/state" / f"{run_id}.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    _ = state.write_text(
        json.dumps({"run_id": run_id, "status": status, "updated_at": event["ended_at"]}),
        encoding="utf-8",
    )


def _mapping(value: JSONValue) -> JSONMap:
    assert isinstance(value, dict)
    return value


def test_report_counts_outcomes_and_keeps_missing_usage_unknown(tmp_path: Path) -> None:
    # Given: one failed run with explicit zero usage and one run with an absent cached field.
    _write_run(tmp_path, "RUN-a", status="failed", duration=100)
    _write_run(tmp_path, "RUN-b", status="passed", duration=300)
    process = tmp_path / ".automation/work/RUN-a/writer/attempt-1/codex-attempt-1.jsonl"
    process.parent.mkdir(parents=True)
    zero = {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "reasoning_output_tokens": 0}}
    unknown = {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}
    _ = process.write_text(json.dumps(zero) + "\n" + json.dumps(unknown) + "\n", encoding="utf-8")

    # When: the read-only report is built over the two runs.
    report = build_report(tmp_path, limit=2)

    # Then: failures remain in the denominator and incomplete usage fields stay unknown.
    assert report["outcomes"] == {"failed": 1, "passed": 1}
    usage = _mapping(report["usage"])
    fields = _mapping(usage["fields"])
    input_field = _mapping(fields["input_tokens"])
    cached_field = _mapping(fields["cached_input_tokens"])
    reasoning_field = _mapping(fields["reasoning_output_tokens"])
    assert input_field["value"] == 10
    assert cached_field["value"] is None
    assert reasoning_field["value"] is None
    assert usage["turns_without_usage"] == 0


def test_report_counts_complete_usage_without_adding_cached_input(tmp_path: Path) -> None:
    # Given: two complete process usage records and a stage with one failed attempt.
    _write_run(tmp_path, "RUN-one", status="failed", duration=100)
    process = tmp_path / ".automation/work/RUN-one/writer/codex-attempt-1.jsonl"
    process.parent.mkdir(parents=True)
    first = {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 40, "reasoning_output_tokens": 7}}
    second = {"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 10, "cached_input_tokens": 10, "reasoning_output_tokens": 3}}
    _ = process.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    # When: the report is calculated.
    report = build_report(tmp_path)

    # Then: input and cached input are separate, and the stage duration remains visible.
    usage = _mapping(report["usage"])
    fields = _mapping(usage["fields"])
    input_field = _mapping(fields["input_tokens"])
    cached_field = _mapping(fields["cached_input_tokens"])
    reasoning_field = _mapping(fields["reasoning_output_tokens"])
    assert input_field["value"] == 150
    assert cached_field["value"] == 50
    assert reasoning_field["value"] == 10
    assert usage["non_cached_input_tokens"] == 100
    stages = report["stages"]
    assert isinstance(stages, list)
    writer = next(_mapping(stage) for stage in stages if _mapping(stage)["stage"] == "writer")
    assert writer["p50_duration_ms"] == 100.0


def test_report_ignores_valid_but_incomplete_final_jsonl_record(tmp_path: Path) -> None:
    # Given: a process log whose final valid JSON object has no record terminator.
    _write_run(tmp_path, "RUN-partial", status="failed", duration=100)
    process = tmp_path / ".automation/work/RUN-partial/writer/codex-attempt-1.jsonl"
    process.parent.mkdir(parents=True)
    event = {"type": "turn.completed", "usage": {"input_tokens": 9, "output_tokens": 1}}
    _ = process.write_text(json.dumps(event), encoding="utf-8")

    # When: the report reads complete JSONL records only.
    report = build_report(tmp_path)

    # Then: the unterminated record is excluded and its usage remains unknown.
    usage = _mapping(report["usage"])
    assert usage["usage_turns"] == 0
    assert usage["incomplete_final_records"] == 1
    fields = _mapping(usage["fields"])
    assert _mapping(fields["input_tokens"])["value"] is None


def test_report_rejects_non_positive_limit(tmp_path: Path) -> None:
    # Given: a report request with an invalid sample limit.
    # When / Then: the caller receives a useful input error before any source write.
    try:
        _ = build_report(tmp_path, limit=0)
    except ValueError as error:
        assert str(error) == "limit must be greater than zero"
    else:
        raise AssertionError("expected ValueError")
