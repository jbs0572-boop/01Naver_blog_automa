from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.codex_process import CodexProcessError, run_codex
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


def _write_staged_result(args: list[str], fixture: Path) -> None:
    stage_cwd = Path(args[args.index("--cd") + 1])
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    for relative in raw["artifacts"]:
        source = fixture.parent / relative
        destination = stage_cwd / "artifacts" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_bytes(source.read_bytes())
        source.unlink()
    output = Path(args[args.index("--output-last-message") + 1])
    _ = output.write_text(json.dumps(raw), encoding="utf-8")


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
        if calls == 2:
            _write_staged_result(args, result_path)
        return subprocess.CompletedProcess(args, int(calls == 1), output, "")

    def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)
    monkeypatch.setattr("tools.codex_process.time.sleep", no_sleep)

    result = CodexStageExecutor().execute(context, result_path)

    assert result.artifacts == ("drafts/test.md",)
    assert calls == 2
    assert (context.work_dir / "attempt-1" / "codex-attempt-1.jsonl").read_text(
        encoding="utf-8"
    ) == "429 Too Many Requests\n"
    assert (context.work_dir / "attempt-1" / "codex-attempt-2.jsonl").is_file()


def test_stage_executor_redacts_loaded_opaque_token_without_child_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, result_path = _prepare_stage(tmp_path, "writer")
    token = "opaque!notion-token?[]"
    observed: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        **_kwargs: SubprocessValue,
    ) -> subprocess.CompletedProcess[str]:
        assert env is not None
        observed.append((args, env))
        _write_staged_result(args, result_path)
        return subprocess.CompletedProcess(args, 0, f"opaque output: {token}", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)

    _ = CodexStageExecutor(sensitive_values=(token,)).execute(context, result_path)

    command, environment = observed[0]
    assert all(token not in argument for argument in command)
    assert all(token not in value for value in environment.values())
    persisted = (context.work_dir / "attempt-1" / "codex-attempt-1.jsonl").read_text(encoding="utf-8")
    assert token not in persisted
    assert persisted == "opaque output: [redacted-notion-token]"


def test_stage_executor_reports_exhausted_429_with_attempt_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _result_path = _prepare_stage(tmp_path, "writer")
    (tmp_path / "drafts/test.md").unlink()

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

    assert (context.work_dir / "attempt-1" / "codex-attempt-3.jsonl").is_file()


def test_run_codex_retries_one_timeout_and_preserves_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(args, 10, output="partial secret_abc\n")
        return subprocess.CompletedProcess(args, 0, "done\n", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)
    monkeypatch.setattr("tools.codex_process.time.sleep", sleeps.append)

    run_codex(
        ["codex"],
        root=tmp_path,
        environment={},
        timeout=10,
        work_dir=tmp_path / "work",
        stage_attempt=2,
    )

    assert calls == 2
    assert sleeps == [10.0]
    attempt_dir = tmp_path / "work" / "attempt-2"
    assert (attempt_dir / "codex-attempt-1.jsonl").read_text() == "partial [redacted-notion-token]\n"
    assert (attempt_dir / "codex-attempt-2.jsonl").read_text() == "done\n"


def test_run_codex_stops_after_two_persistent_timeouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: every process attempt times out after producing partial output.
    calls = 0

    def timed_out(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(args, 10, output=f"partial-{calls}\n")

    def no_sleep(_delay: float) -> None:
        return

    monkeypatch.setattr("tools.codex_process.subprocess.run", timed_out)
    monkeypatch.setattr("tools.codex_process.time.sleep", no_sleep)

    # When: the transport timeout budget is exhausted.
    with pytest.raises(CodexProcessError) as caught:
        run_codex(
            ["codex"],
            root=tmp_path,
            environment={},
            timeout=10,
            work_dir=tmp_path / "work",
        )

    # Then: exactly one timeout retry occurred and both outputs were captured.
    assert calls == 2
    assert caught.value.error_type == "process_timeout"
    assert caught.value.attempts == 2
    attempt_dir = tmp_path / "work" / "attempt-1"
    assert (attempt_dir / "codex-attempt-1.jsonl").read_text() == "partial-1\n"
    assert (attempt_dir / "codex-attempt-2.jsonl").read_text() == "partial-2\n"


def test_run_codex_does_not_retry_missing_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_run(_args: list[str], **_kwargs: SubprocessValue) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        raise FileNotFoundError("codex")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)

    with pytest.raises(CodexProcessError) as caught:
        run_codex(
            ["codex"],
            root=tmp_path,
            environment={},
            timeout=10,
            work_dir=tmp_path / "work",
            stage_attempt=1,
        )

    assert calls == 1
    assert caught.value.error_type == "process_unavailable"
    assert caught.value.retryable is False
