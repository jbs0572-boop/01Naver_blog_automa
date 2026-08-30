from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.codex_stage_executor import CodexStageExecutor, StageExecutionError
from tools.runner_types import StageExecutionContext

SubprocessValue = str | Path | list[str] | int | bool | None


def _prepare_stage(root: Path, stage: str) -> tuple[StageExecutionContext, Path]:
    _ = (root / f"{stage}.md").write_text("instruction", encoding="utf-8")
    schema_dir = root / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    artifact = (
        root / "research" / "test.md"
        if stage == "researcher"
        else root / "drafts" / "test.md"
    )
    artifact.parent.mkdir()
    _ = artifact.write_text(stage, encoding="utf-8")
    result_path = root / "result.json"
    _ = result_path.write_text(
        json.dumps(
            {
                "stage": stage,
                "status": "passed",
                "execution": "produced",
                "artifacts": [artifact.relative_to(root).as_posix()],
            }
        ),
        encoding="utf-8",
    )
    return (
        StageExecutionContext(
            root,
            stage,
            "RUN-test",
            "TOPIC-test",
            "test",
            root / ".automation" / "work",
        ),
        result_path,
    )


def test_researcher_command_requires_serial_lanes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, result_path = _prepare_stage(tmp_path, "researcher")
    calls: list[list[str]] = []

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)

    _ = CodexStageExecutor().execute(context, result_path)

    prompt = calls[0][-1]
    assert "Do not spawn subagents" in prompt
    assert "serially" in prompt


def test_stage_executor_retries_429_and_preserves_attempt_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, result_path = _prepare_stage(tmp_path, "writer")
    calls = 0

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        output = "429 Too Many Requests\n" if calls == 1 else "task complete\n"
        return subprocess.CompletedProcess(args, int(calls == 1), output, "")

    def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)
    monkeypatch.setattr("tools.codex_process.time.sleep", no_sleep)

    result = CodexStageExecutor().execute(context, result_path)

    assert result.artifacts == ("drafts/test.md",)
    assert calls == 2
    assert (context.work_dir / "codex-attempt-1.jsonl").read_text(
        encoding="utf-8"
    ) == "429 Too Many Requests\n"
    assert (context.work_dir / "codex-attempt-2.jsonl").is_file()


def test_stage_executor_reports_exhausted_429_with_attempt_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _result_path = _prepare_stage(tmp_path, "writer")

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "429 Too Many Requests\n", "")

    def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)
    monkeypatch.setattr("tools.codex_process.time.sleep", no_sleep)

    with pytest.raises(StageExecutionError, match=r"429.*codex-attempt-3\.jsonl"):
        _ = CodexStageExecutor().execute(context)

    assert (context.work_dir / "codex-attempt-3.jsonl").is_file()
