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
    assert run.historical is True


def test_dashboard_keeps_malformed_log_visible_as_error(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "RUN-bad.jsonl"
    path.parent.mkdir(parents=True)
    _ = path.write_text("not-json\n", encoding="utf-8")
    runs = load_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error == "읽기 실패: JSONDecodeError"


def test_demo_dataset_is_explicit_and_in_memory(tmp_path: Path) -> None:
    runs = load_runs(tmp_path, demo=True)
    assert {run.status for run in runs} == {
        "ready_for_naver",
        "awaiting_user_confirmation",
        "failed",
    }
    assert not (tmp_path / "runs").exists()
