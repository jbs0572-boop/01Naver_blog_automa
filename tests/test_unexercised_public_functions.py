"""Behavioral assertions for previously uninvoked public helper bodies."""

from pathlib import Path

import pytest

from tools import runner_execution
from tools.external_adapter import ExternalAction, ExternalSystem, ExternalWritePlan


def test_external_write_plan_serializes_explicit_nonexecuting_contract():
    plan = ExternalWritePlan(
        system=ExternalSystem.NOTION,
        action=ExternalAction.NOTION_WRITE,
        run_id="run-1",
        target_id="database-1",
        artifact_digest="abc123",
        artifact_paths=("final/post.md", "assets/post/thumbnail.png"),
        dry_run=True,
        would_execute=False,
    )
    assert plan.as_json() == {
        "system": "notion",
        "action": "notion_write",
        "run_id": "run-1",
        "target_id": "database-1",
        "artifact_digest": "abc123",
        "artifact_paths": ["final/post.md", "assets/post/thumbnail.png"],
        "dry_run": True,
        "would_execute": False,
    }


def test_get_status_is_read_only_and_returns_persisted_state(tmp_path, monkeypatch):
    expected_path = tmp_path / "state.json"
    expected = {"run_id": "one", "status": "blocked"}
    calls = []

    def fake_paths(root, run_id, state_dir):
        calls.append((root, run_id, state_dir))
        return expected_path, tmp_path / "run.jsonl", tmp_path / "lock.json"

    def fake_read(path):
        assert path == expected_path
        return expected

    monkeypatch.setattr(runner_execution, "state_paths", fake_paths)
    monkeypatch.setattr(runner_execution, "read_state", fake_read)
    assert runner_execution.get_status(tmp_path, "one", tmp_path / "storage") is expected
    assert calls == [(tmp_path, "one", tmp_path / "storage")]


@pytest.mark.parametrize("input_value,expected", [
    ("timeout", True),
    ("TIMEOUT", True),
    ("temporary_unavailable", True),
    ("io_error", True),
    ("http-500", False),
    ("timeout ", False),
    ("", False),
    (OSError("disk"), True),
    (PermissionError("denied"), True),
    (TimeoutError(), True),
    (ValueError("bad"), False),
    (Exception("unknown"), False),
])
def test_transient_error_classification_is_explicit(input_value, expected):
    assert runner_execution.is_transient_error(input_value) is expected
