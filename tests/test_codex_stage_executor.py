from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.codex_stage_executor import CodexStageExecutor, StageExecutionError
from tools.runner_types import StageExecutionContext

SubprocessValue = str | Path | list[str] | int | bool | None


def _stage_context(root: Path, stage: str = "writer") -> StageExecutionContext:
    return StageExecutionContext(
        root=root,
        stage=stage,
        run_id="RUN-test",
        topic_id="TOPIC-test",
        keyword="test",
        work_dir=root / ".automation" / "work",
    )


def test_stage_executor_rejects_malformed_codex_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = (tmp_path / "writer.md").write_text("instruction", encoding="utf-8")
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    result_path = tmp_path / "result.json"
    _ = result_path.write_text(json.dumps({"status": "passed"}), encoding="utf-8")

    def fake_run(
        *_args: SubprocessValue, **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)
    executor = CodexStageExecutor(codex_binary="codex")

    with pytest.raises(StageExecutionError, match="stage result"):
        _ = executor.execute(_stage_context(tmp_path), result_path)


def test_stage_executor_builds_strict_structured_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = (tmp_path / "writer.md").write_text("instruction", encoding="utf-8")
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    result_path = tmp_path / "result.json"
    output = {
        "stage": "writer",
        "status": "passed",
        "execution": "produced",
        "artifacts": ["drafts/test.md"],
    }
    _ = result_path.write_text(json.dumps(output), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    draft = tmp_path / "drafts" / "test.md"
    draft.parent.mkdir()
    _ = draft.write_text("draft", encoding="utf-8")
    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)

    _ = CodexStageExecutor().execute(_stage_context(tmp_path), result_path)

    assert calls
    assert calls[0][:2] == ["codex", "exec"]
    assert "--strict-config" in calls[0]
    assert "--json" in calls[0]
    assert "--output-schema" in calls[0]
    assert "--output-last-message" in calls[0]
    assert "--cd" in calls[0]


def test_stage_executor_normalizes_absolute_artifact_paths_inside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = (tmp_path / "writer.md").write_text("instruction", encoding="utf-8")
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    draft = tmp_path / "drafts" / "test.md"
    draft.parent.mkdir()
    _ = draft.write_text("draft", encoding="utf-8")
    result_path = tmp_path / "result.json"
    _ = result_path.write_text(
        json.dumps(
            {
                "stage": "writer",
                "status": "passed",
                "execution": "produced",
                "artifacts": [str(draft)],
            }
        ),
        encoding="utf-8",
    )

    def fake_run(
        *_args: SubprocessValue, **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)
    result = CodexStageExecutor().execute(_stage_context(tmp_path), result_path)

    assert result.artifacts == ("drafts/test.md",)


def test_stage_executor_accepts_content_assembler_outputs_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyword = "test"
    run_id = "RUN-test"
    _ = (tmp_path / "content-assembler.md").write_text(
        "instruction", encoding="utf-8"
    )
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    final_paths = tuple(
        tmp_path / "final" / f"{keyword}{suffix}"
        for suffix in (
            ".md",
            "-naver-layout.md",
            "-naver-copy.md",
            "-naver-input.md",
        )
    )
    for path in final_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text("content", encoding="utf-8")
    manifest = tmp_path / "manifests" / f"{run_id}-workflow-manifest.json"
    manifest.parent.mkdir()
    _ = manifest.write_text("manifest", encoding="utf-8")
    result_path = (
        tmp_path
        / ".automation"
        / "work"
        / run_id
        / "content-assembler"
        / "stage-result.json"
    )
    result_path.parent.mkdir(parents=True)
    _ = result_path.write_text(
        json.dumps(
            {
                "stage": "content-assembler",
                "status": "passed",
                "execution": "produced",
                "artifacts": [
                    *(str(path) for path in final_paths),
                    str(manifest),
                    str(result_path),
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_run(
        *_args: SubprocessValue, **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)
    context = StageExecutionContext(
        tmp_path,
        "content-assembler",
        run_id,
        "TOPIC-test",
        keyword,
        tmp_path / ".automation" / "work",
    )
    result = CodexStageExecutor().execute(context, result_path)

    expected_artifacts = tuple(
        f"final/{keyword}{suffix}"
        for suffix in (
            ".md",
            "-naver-layout.md",
            "-naver-copy.md",
            "-naver-input.md",
        )
    ) + (f"manifests/{run_id}-workflow-manifest.json",)
    assert result.artifacts == expected_artifacts
