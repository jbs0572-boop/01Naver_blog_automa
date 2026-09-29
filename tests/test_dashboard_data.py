from __future__ import annotations

import json
from pathlib import Path

from tools.dashboard_data import load_runs, snapshot


def _write_log(root: Path, run_id: str, status: str = "passed") -> None:
    path = root / "runs" / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event_type": "stage",
        "run_id": run_id,
        "topic_id": "TOPIC-dashboard",
        "stage": "content-assembler",
        "status": status,
        "started_at": "2026-08-29T00:00:00+00:00",
        "ended_at": "2026-08-29T00:01:00+00:00",
        "attempt": 1,
        "quality": {"q1": "passed"},
    }
    _ = path.write_text(json.dumps(event) + "\n", encoding="utf-8")


def test_snapshot_aggregates_logs_without_exposing_raw_events(tmp_path: Path) -> None:
    _write_log(tmp_path, "RUN-dashboard")
    result = snapshot(tmp_path)
    assert result["summary"] == {"total": 1, "by_status": {"passed": 1}}
    runs = result["runs"]
    assert isinstance(runs, list)
    first = runs[0]
    assert isinstance(first, dict)
    assert first["q1"] == "passed"
    assert "quality" not in first
    assert "mode" not in first
    assert first["topic_source"] is None


def test_dashboard_derives_gates_from_production_stage_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runs" / "RUN-production-gates.jsonl"
    path.parent.mkdir(parents=True)
    events = [
        {
            "event_type": "stage",
            "run_id": "RUN-production-gates",
            "topic_id": "TOPIC-production-gates",
            "stage": "content-assembler",
            "status": "passed",
            "attempt": 1,
            "quality": {"artifact_digest": "sha256:artifact"},
        },
        {
            "event_type": "stage",
            "run_id": "RUN-production-gates",
            "topic_id": "TOPIC-production-gates",
            "stage": "notion-rider",
            "status": "passed",
            "attempt": 1,
            "quality": {"storage_integrity": "passed"},
        },
    ]
    _ = path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    run = load_runs(tmp_path)[0]

    assert run.q1 == "passed"
    assert run.q2 == "passed"


def test_snapshot_exposes_topic_source_without_legacy_mode(tmp_path: Path) -> None:
    _write_log(tmp_path, "RUN-auto")
    state_path = tmp_path / ".automation" / "state" / "RUN-auto.json"
    state_path.parent.mkdir(parents=True)
    _ = state_path.write_text(
        json.dumps({"run_id": "RUN-auto", "status": "passed", "auto_topic": True}),
        encoding="utf-8",
    )

    run = load_runs(tmp_path)[0]

    assert run.topic_source == "auto_selected"
    assert not hasattr(run, "mode")


def test_snapshot_prefers_explicit_topic_source_over_legacy_boolean(
    tmp_path: Path,
) -> None:
    _write_log(tmp_path, "RUN-user")
    state_path = tmp_path / ".automation" / "state" / "RUN-user.json"
    state_path.parent.mkdir(parents=True)
    _ = state_path.write_text(
        json.dumps(
            {
                "run_id": "RUN-user",
                "status": "passed",
                "topic_source": "user_defined",
                "auto_topic": True,
                "mode": "legacy",
            }
        ),
        encoding="utf-8",
    )

    run = load_runs(tmp_path)[0]

    assert run.topic_source == "user_defined"
    assert run.historical is False


def test_dashboard_keeps_malformed_log_visible_as_error(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "RUN-bad.jsonl"
    path.parent.mkdir(parents=True)
    _ = path.write_text("not-json\n", encoding="utf-8")
    runs = load_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error == "읽기 실패: JSONDecodeError"


def test_empty_project_has_no_synthetic_runtime_dataset(tmp_path: Path) -> None:
    runs = load_runs(tmp_path)
    assert runs == ()
    assert not (tmp_path / "runs").exists()


def test_stage_view_exposes_cumulative_attempt_metrics_and_error_guidance(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "RUN-metrics.jsonl"
    path.parent.mkdir(parents=True)
    events = [
        {
            "event_type": "stage", "run_id": "RUN-metrics", "topic_id": "TOPIC-metrics",
            "stage": "writer", "status": "failed", "started_at": "2026-09-11T00:00:00+00:00",
            "ended_at": "2026-09-11T00:00:02+00:00", "duration_ms": 2000, "attempt": 1,
            "error_type": "temporary_io", "error_message_safe": "disk busy",
        },
        {
            "event_type": "stage", "run_id": "RUN-metrics", "topic_id": "TOPIC-metrics",
            "stage": "writer", "status": "passed", "started_at": "2026-09-11T00:00:03+00:00",
            "ended_at": "2026-09-11T00:00:04+00:00", "duration_ms": 1000, "attempt": 2,
            "model": "gpt-test", "reasoning_effort": "high", "error_type": None,
            "error_message_safe": "passed",
        },
    ]
    _ = path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    writer = next(stage for stage in load_runs(tmp_path)[0].stages if stage.name == "writer")

    assert writer.attempt == 2
    assert writer.total_attempts == 2
    assert writer.duration_ms == 3000
    assert writer.last_duration_ms == 1000
    assert writer.model == "gpt-test"
    assert writer.attempts[0].error_type == "temporary_io"
    assert writer.attempts[0].next_action


def test_stage_views_merge_current_state_with_log_attempt_history(tmp_path: Path) -> None:
    _write_log(tmp_path, "RUN-merge", status="failed")
    state_path = tmp_path / ".automation" / "state" / "RUN-merge.json"
    state_path.parent.mkdir(parents=True)
    _ = state_path.write_text(
        json.dumps(
            {
                "run_id": "RUN-merge",
                "status": "running",
                "stages": {
                    "content-assembler": "running",
                    "notion-rider": "skipped",
                    "naver-rider": "skipped",
                },
            }
        ),
        encoding="utf-8",
    )

    run = load_runs(tmp_path)[0]
    content = next(stage for stage in run.stages if stage.name == "content-assembler")
    notion = next(stage for stage in run.stages if stage.name == "notion-rider")

    assert content.status == "running"
    assert content.message is None
    assert content.error_type is None
    assert content.attempts[0].status == "failed"
    assert notion.status == "skipped"


def test_state_stage_statuses_include_validated_and_skipped_stages(tmp_path: Path) -> None:
    _write_log(tmp_path, "RUN-state")
    state_path = tmp_path / ".automation" / "state" / "RUN-state.json"
    state_path.parent.mkdir(parents=True)
    _ = state_path.write_text(
        json.dumps(
            {
                "run_id": "RUN-state",
                "status": "failed",
                "stages": {
                    "topic-selector": "validated",
                    "content-assembler": "passed",
                    "notion-rider": "skipped",
                    "naver-rider": "skipped",
                },
            }
        ),
        encoding="utf-8",
    )

    stages = {stage.name: stage.status for stage in load_runs(tmp_path)[0].stages}

    assert stages["topic-selector"] == "validated"
    assert stages["notion-rider"] == "skipped"
    assert stages["naver-rider"] == "skipped"


def test_run_exposes_separate_start_and_terminal_end_timestamps(tmp_path: Path) -> None:
    _write_log(tmp_path, "RUN-timestamps")
    state_path = tmp_path / ".automation" / "state" / "RUN-timestamps.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _ = state_path.write_text(
        json.dumps({"run_id": "RUN-timestamps", "status": "failed", "updated_at": "2026-08-29T00:02:00+00:00"}),
        encoding="utf-8",
    )

    run = load_runs(tmp_path)[0]

    assert run.started_at == "2026-08-29T00:00:00+00:00"
    assert run.ended_at == "2026-08-29T00:01:00+00:00"
    assert run.updated_at == "2026-08-29T00:02:00+00:00"
