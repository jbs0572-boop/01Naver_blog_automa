import json
from pathlib import Path

from tools.dashboard_usage import usage_by_stage, usage_for


def test_usage_counts_turns_without_double_counting_cached_input(tmp_path: Path) -> None:
    path = tmp_path / ".automation/work/RUN-test/writer/codex-attempt-1.jsonl"
    path.parent.mkdir(parents=True)
    event = {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 60}}
    _ = path.write_text(json.dumps(event) + "\n" + json.dumps(event) + "\npartial", encoding="utf-8")
    usage = usage_for(tmp_path, "RUN-test")
    assert usage is not None
    assert {key: usage[key] for key in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens", "recorded_turns")} == {"input_tokens": 200, "output_tokens": 40, "cached_input_tokens": 120, "total_tokens": 240, "recorded_turns": 2}
    assert usage["usage_state"] == "observed"


def test_missing_usage_is_unknown(tmp_path: Path) -> None:
    assert usage_for(tmp_path, "RUN-missing") is None
    assert usage_for(tmp_path, "../outside") is None


def test_usage_groups_nested_attempts_and_prefers_them_over_legacy_copy(tmp_path: Path) -> None:
    work = tmp_path / ".automation/work/RUN-test/writer"
    nested = work / "attempt-2/codex-attempt-1.jsonl"
    nested.parent.mkdir(parents=True)
    event = {"type": "turn.completed", "usage": {"input_tokens": 30, "output_tokens": 7, "cached_input_tokens": 20}}
    _ = nested.write_text(json.dumps(event) + "\n", encoding="utf-8")
    _ = (work / "codex-attempt-1.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    stages = usage_by_stage(tmp_path, "RUN-test")

    assert stages["writer"]["total_tokens"] == 37
    assert stages["writer"]["recorded_turns"] == 1
    assert stages["writer"]["process_attempts"] == 1
    total = usage_for(tmp_path, "RUN-test")
    assert total is not None
    assert total["total_tokens"] == 37


def test_usage_accepts_completed_append_once_and_resets_after_truncate(tmp_path: Path) -> None:
    path = tmp_path / ".automation/work/RUN-live/writer/codex-attempt-1.jsonl"
    path.parent.mkdir(parents=True)
    event = {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 4}}
    encoded = json.dumps(event)
    _ = path.write_text(encoded[:20], encoding="utf-8")
    assert usage_for(tmp_path, "RUN-live") is None

    with path.open("a", encoding="utf-8") as stream:
        _ = stream.write(encoded[20:] + "\n")
    first = usage_for(tmp_path, "RUN-live")
    second = usage_for(tmp_path, "RUN-live")
    assert first == second
    assert first is not None and first["total_tokens"] == 12

    replacement = {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1, "cached_input_tokens": 0}}
    _ = path.write_text(json.dumps(replacement) + "\n", encoding="utf-8")
    reset = usage_for(tmp_path, "RUN-live")
    assert reset is not None
    assert reset["total_tokens"] == 4
    assert reset["recorded_turns"] == 1
