from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

import pytest

from tools.codex_stage_command import stage_prompt
from tools.codex_stage_executor import (
    STAGE_TIMEOUTS,
    CodexStageExecutor,
    StageExecutionError,
)
from tools.contract_types import ContractError, JSONMap
from tools.notion_copy_normalizer import normalize_naver_copy_text
from tools.notion_copy_parser import parse_naver_copy
from tools.research_readiness import ResearchReadiness
from tools.runner_types import (
    RunStatus,
    StageExecution,
    StageExecutionContext,
    TopicSelectionContext,
)
from tools.topic_metadata import (
    CreatorAdvisorCandidate,
    CreatorAdvisorSnapshot,
    snapshot_sha256,
    write_snapshot,
)

SubprocessValue = str | Path | list[str] | int | bool | None


@pytest.fixture(autouse=True)
def isolate_browser_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    def capture() -> JSONMap:
        return {"capture_id": "RAW-fixture", "tree": "가을 여행 · 서울 축제", "source_url": "https://creator-advisor.naver.com/naver_blog/sola_note"}
    monkeypatch.setattr("tools.codex_topic_selection.capture_creator_advisor", capture)


def test_notion_stage_uses_documented_fifteen_minute_budget() -> None:
    assert STAGE_TIMEOUTS["notion-rider"] == 15 * 60


def test_image_stage_uses_audited_thirty_five_minute_budget() -> None:
    assert STAGE_TIMEOUTS["image-maker"] == 35 * 60


def _stage_context(
    root: Path,
    stage: str = "writer",
    q1_feedback: str | None = None,
) -> StageExecutionContext:
    return StageExecutionContext(
        root=root,
        stage=stage,
        run_id="RUN-test",
        topic_id="TOPIC-test",
        keyword="test",
        work_dir=root / ".automation" / "work",
        q1_feedback=q1_feedback,
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


def test_stage_executor_rejects_error_report_with_success_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _writer_stage_files(tmp_path)

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        workspace_root = Path(args[args.index("--cd") + 1])
        artifact = workspace_root / "artifacts" / "drafts" / "test.md"
        artifact.parent.mkdir(parents=True)
        _ = artifact.write_text("blocked draft", encoding="utf-8")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "writer",
                    "status": "validated",
                    "execution": "produced",
                    "artifacts": ["drafts/test.md"],
                    "message": "오류: writer - 선행 Gate 실패",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)

    with pytest.raises(
        StageExecutionError, match="success status cannot report an error"
    ):
        _ = CodexStageExecutor().execute(_stage_context(tmp_path))
    assert not (tmp_path / "drafts" / "test.md").exists()


def test_stage_executor_rejects_validated_execution_for_producer_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a producer reports validation while staging its canonical artifact.
    _writer_stage_files(tmp_path)

    def validated_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        workspace_root = Path(args[args.index("--cd") + 1])
        artifact = workspace_root / "artifacts" / "drafts" / "test.md"
        artifact.parent.mkdir(parents=True)
        _ = artifact.write_text("draft", encoding="utf-8")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "writer",
                    "status": "passed",
                    "execution": "validated",
                    "artifacts": ["drafts/test.md"],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", validated_run)

    # When/Then: the executor rejects the false success before promotion.
    with pytest.raises(
        StageExecutionError, match="producer stage execution must be produced"
    ):
        _ = CodexStageExecutor().execute(_stage_context(tmp_path))
    assert not (tmp_path / "drafts" / "test.md").exists()


def test_stage_executor_cleans_staging_after_process_failure(
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
    stage_roots: list[Path] = []

    def failed_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        stage_roots.append(Path(args[args.index("--cd") + 1]))
        return subprocess.CompletedProcess(args, 1, "failed", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", failed_run)

    with pytest.raises(StageExecutionError):
        _ = CodexStageExecutor().execute(_stage_context(tmp_path))

    assert stage_roots and not stage_roots[0].exists()


def test_stage_executor_ignores_workspace_helpers_outside_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a stage workspace contains disposable helper and abandoned-attempt files.
    _writer_stage_files(tmp_path)
    user_file = tmp_path / "notes" / "user-owned.md"
    user_file.parent.mkdir()
    _ = user_file.write_bytes(b"keep")
    workspace_roots: list[Path] = []

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        workspace_root = Path(args[args.index("--cd") + 1])
        workspace_roots.append(workspace_root)
        _ = (workspace_root / "helper.py").write_text("scratch", encoding="utf-8")
        abandoned = workspace_root / "attempts" / "abandoned.md"
        abandoned.parent.mkdir()
        _ = abandoned.write_text("discard", encoding="utf-8")
        artifact = workspace_root / "artifacts" / "drafts" / "test.md"
        artifact.parent.mkdir(parents=True)
        _ = artifact.write_text("draft", encoding="utf-8")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "writer",
                    "status": "passed",
                    "execution": "produced",
                    "artifacts": ["drafts/test.md"],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)

    # When: the executor promotes the declared artifact.
    result = CodexStageExecutor().execute(_stage_context(tmp_path))

    # Then: only the canonical artifact is promoted and all disposable state is removed.
    assert result.artifacts == ("drafts/test.md",)
    assert (tmp_path / "drafts" / "test.md").read_text(encoding="utf-8") == "draft"
    assert user_file.read_bytes() == b"keep"
    assert not (tmp_path / "helper.py").exists()
    assert workspace_roots and not workspace_roots[0].exists()


def test_stage_executor_rejects_undeclared_file_inside_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the dedicated artifact tree contains one undeclared file.
    _writer_stage_files(tmp_path)
    workspace_roots: list[Path] = []

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        workspace_root = Path(args[args.index("--cd") + 1])
        workspace_roots.append(workspace_root)
        artifacts_root = workspace_root / "artifacts"
        artifact = artifacts_root / "drafts" / "test.md"
        artifact.parent.mkdir(parents=True)
        _ = artifact.write_text("draft", encoding="utf-8")
        _ = (artifacts_root / "helper.py").write_text("scratch", encoding="utf-8")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "writer",
                    "status": "passed",
                    "execution": "produced",
                    "artifacts": ["drafts/test.md"],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)

    # When/Then: strict exact-set validation rejects contamination inside artifacts.
    with pytest.raises(
        ContractError, match="staging contains undeclared or missing artifacts"
    ):
        _ = CodexStageExecutor().execute(_stage_context(tmp_path))
    assert not (tmp_path / "drafts" / "test.md").exists()
    assert workspace_roots and not workspace_roots[0].exists()


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
        supplied_schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        assert supplied_schema["properties"]["execution"]["enum"] == ["produced", "attempted"]
        stage_cwd = Path(args[args.index("--cd") + 1])
        staged = stage_cwd / "artifacts" / "drafts" / "test.md"
        staged.parent.mkdir(parents=True)
        _ = staged.write_text("draft", encoding="utf-8")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(json.dumps(output), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)

    _ = CodexStageExecutor().execute(
        _stage_context(tmp_path, q1_feedback="api_key=super-secret-value"), result_path
    )

    assert calls
    assert calls[0][:2] == ["codex", "exec"]
    assert "--strict-config" in calls[0]
    assert "--ignore-user-config" in calls[0]
    assert "--ignore-rules" in calls[0]
    assert "--ephemeral" in calls[0]
    assert calls[0][calls[0].index("--profile") + 1] == "naver-automation"
    assert calls[0][calls[0].index("--sandbox") + 1] == "workspace-write"
    assert "--json" in calls[0]
    assert "--output-schema" in calls[0]
    assert "--output-last-message" in calls[0]
    assert "--cd" in calls[0]
    assert "--skip-git-repo-check" in calls[0]
    stage_cwd = Path(calls[0][calls[0].index("--cd") + 1])
    assert not stage_cwd.is_relative_to(tmp_path)
    assert not stage_cwd.exists()
    assert all("super-secret-value" not in value for value in calls[0])
    assert any("api_key=[redacted]" in value for value in calls[0])


def test_stage_executor_rejects_absolute_artifact_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _writer_stage_files(tmp_path)
    draft = tmp_path / "drafts" / "test.md"
    result_path = tmp_path / "result.json"
    work_dir = tmp_path / ".automation" / "work"
    workspace_roots: list[Path] = []
    calls: list[list[str]] = []

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        workspace_root = Path(args[args.index("--cd") + 1])
        workspace_roots.append(workspace_root)
        staged = workspace_root / "artifacts" / "drafts" / "test.md"
        staged.parent.mkdir(parents=True)
        _ = staged.write_text("staged draft", encoding="utf-8")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
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
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)
    assert not draft.exists()
    with pytest.raises(ContractError) as error:
        _ = CodexStageExecutor().execute(_stage_context(tmp_path), result_path)
    assert str(error.value) == f"stage artifact path is unsafe: {draft}"
    assert len(calls) == 1
    assert not draft.exists()
    assert not result_path.exists()
    assert not (work_dir / "artifact-ownership.json").exists()
    assert workspace_roots and not workspace_roots[0].exists()


def test_stage_executor_reuses_existing_writer_draft_without_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _writer_stage_files(tmp_path)
    draft = tmp_path / "drafts" / "test.md"
    draft.parent.mkdir()
    original = b"existing draft bytes\n"
    _ = draft.write_bytes(original)
    work_dir = tmp_path / ".automation" / "work"

    def unexpected_run(
        *_args: SubprocessValue, **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        pytest.fail("writer reuse must not invoke subprocess")

    monkeypatch.setattr("tools.codex_process.subprocess.run", unexpected_run)

    result = CodexStageExecutor().execute(_stage_context(tmp_path))

    assert result.status is RunStatus.PASSED
    assert result.execution is StageExecution.PRODUCED
    assert result.message == "기존 초안 재사용"
    assert result.artifacts == ()
    assert draft.read_bytes() == original
    assert not (work_dir / "artifact-ownership.json").exists()


def test_writer_regenerates_existing_draft_when_run_has_fresh_research_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _writer_stage_files(tmp_path)
    draft = tmp_path / "drafts" / "test.md"
    draft.parent.mkdir()
    _ = draft.write_text("stale draft", encoding="utf-8")
    revision = tmp_path / "research" / "revisions" / "RUN-test" / "test.md"
    revision.parent.mkdir(parents=True)
    _ = revision.write_text("fresh research", encoding="utf-8")
    prompts: list[str] = []

    def current_revision_is_ready(_path: Path) -> ResearchReadiness:
        return ResearchReadiness("ready", 1, ())

    monkeypatch.setattr(
        "tools.codex_stage_executor.require_research_readiness",
        current_revision_is_ready,
    )

    def produce_fresh_draft(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        prompts.append(" ".join(str(arg) for arg in args))
        workspace_root = Path(args[args.index("--cd") + 1])
        artifact = workspace_root / "artifacts" / "drafts" / "test.md"
        artifact.parent.mkdir(parents=True)
        _ = artifact.write_text("fresh draft", encoding="utf-8")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "writer",
                    "status": "passed",
                    "execution": "produced",
                    "artifacts": ["drafts/test.md"],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", produce_fresh_draft)

    result = CodexStageExecutor().execute(_stage_context(tmp_path))

    assert result.execution is StageExecution.PRODUCED
    assert draft.read_text(encoding="utf-8") == "fresh draft"
    assert (
        tmp_path
        / ".automation"
        / "archive"
        / "stage-artifacts"
        / "writer"
        / "test"
        / "RUN-test"
        / "test.md.previous"
    ).read_text(encoding="utf-8") == "stale draft"
    assert len(prompts) == 1
    assert str(revision) in prompts[0]


def test_stage_executor_exposes_user_local_bin_to_isolated_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _writer_stage_files(tmp_path)
    environments: list[dict[str, str]] = []
    monkeypatch.setenv("HOME", "/Users/tester")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    def capture_run(
        args: list[str], **kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        environments.append(environment)
        return _successful_writer_run(args, **kwargs)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    _ = CodexStageExecutor().execute(_stage_context(tmp_path))

    assert environments[0]["PATH"].split(":", 1)[0] == "/Users/tester/.local/bin"


def test_stage_executor_accepts_only_content_assembler_final_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyword = "test"
    run_id = "RUN-test"
    _ = (tmp_path / "content-assembler.md").write_text("instruction", encoding="utf-8")
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
                    str(result_path),
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        stage_cwd = Path(args[args.index("--cd") + 1])
        staged_paths = tuple(
            stage_cwd / "artifacts" / "final" / f"{keyword}{suffix}"
            for suffix in (
                ".md",
                "-naver-layout.md",
                "-naver-copy.md",
                "-naver-input.md",
            )
        )
        for path in staged_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text("content", encoding="utf-8")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "content-assembler",
                    "status": "passed",
                    "execution": "produced",
                    "artifacts": [
                        *(
                            path.relative_to(stage_cwd / "artifacts").as_posix()
                            for path in staged_paths
                        ),
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

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
    )
    assert result.artifacts == expected_artifacts


def test_content_assembler_migrates_logical_final_outputs_into_staging_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the model follows content-assembler.md's logical final/ path inside its
    # disposable workspace instead of the host's required artifacts/final/ prefix.
    keyword = "kfc 1+1"
    _ = (tmp_path / "content-assembler.md").write_text("instruction", encoding="utf-8")
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    def logical_path_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        workspace_root = Path(args[args.index("--cd") + 1])
        output_dir = workspace_root / "final"
        output_dir.mkdir()
        declared = [
            f"final/{keyword}{suffix}"
            for suffix in (
                ".md",
                "-naver-layout.md",
                "-naver-copy.md",
                "-naver-input.md",
            )
        ]
        for relative in declared:
            _ = (workspace_root / relative).write_text(
                "[TITLE]kfc 1+1[/TITLE]\n[TEXT]body[/TEXT]\n",
                encoding="utf-8",
            )
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "content-assembler",
                    "status": "passed",
                    "execution": "produced",
                    "artifacts": declared,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", logical_path_run)
    context = StageExecutionContext(
        tmp_path,
        "content-assembler",
        "RUN-test",
        "TOPIC-test",
        keyword,
        tmp_path / ".automation" / "work",
    )

    # When: the host processes the produced result.
    result = CodexStageExecutor().execute(context)

    # Then: genuine model outputs are moved inside the controlled artifact root and
    # promoted; no source-project output is synthesized.
    assert result.artifacts == tuple(
        f"final/{keyword}{suffix}"
        for suffix in (
            ".md",
            "-naver-layout.md",
            "-naver-copy.md",
            "-naver-input.md",
        )
    )
    assert (tmp_path / "final" / f"{keyword}-naver-layout.md").is_file()


@pytest.mark.parametrize(
    "list_attributes",
    ("ordered=true", "ordered=false", "type=ordered", "type=unordered"),
)
def test_content_assembler_normalizes_observed_bracket_variants_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    list_attributes: str,
) -> None:
    # Given: the child emits the three malformed bracket forms observed in live Q1.
    keyword = "test"
    _ = (tmp_path / "content-assembler.md").write_text("instruction", encoding="utf-8")
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    asset_dir = tmp_path / "assets" / keyword
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "thumbnail.png").write_bytes(b"image")
    malformed = f"""[TITLE]축제 안내[/TITLE]
[IMAGE file="../assets/test/thumbnail.png" alt="대표 이미지" representative=true]
[ALT]대표 이미지[/ALT]
[LIST {list_attributes}]
[ITEM]첫 항목[/ITEM]
[/LIST]
[TABLE]
[ROW]구분 | 확인된 내용[/ROW]
[ROW]행사명 | 꽃축제[/ROW]
[/TABLE]
"""

    def fake_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        stage_cwd = Path(args[args.index("--cd") + 1])
        artifacts = stage_cwd / "artifacts"
        declared = [
            f"final/{keyword}{suffix}"
            for suffix in (
                ".md",
                "-naver-layout.md",
                "-naver-copy.md",
                "-naver-input.md",
            )
        ]
        for relative in declared:
            path = artifacts / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(
                "assembled" if relative.endswith(f"{keyword}.md") else malformed,
                encoding="utf-8",
            )
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "content-assembler",
                    "status": "passed",
                    "execution": "produced",
                    "artifacts": declared,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", fake_run)
    context = StageExecutionContext(
        tmp_path,
        "content-assembler",
        "RUN-test",
        "TOPIC-test",
        keyword,
        tmp_path / ".automation" / "work",
    )

    # When: trusted host execution accepts the staged four-file output.
    _ = CodexStageExecutor().execute(context)

    # Then: every Naver derivative is canonical and accepted by the strict parser.
    derivatives = tuple(
        tmp_path / "final" / f"{keyword}{suffix}"
        for suffix in ("-naver-layout.md", "-naver-copy.md", "-naver-input.md")
    )
    parsed = tuple(parse_naver_copy(path) for path in derivatives)
    assert parsed[0] == parsed[1] == parsed[2]
    assert (
        derivatives[0].read_text(encoding="utf-8")
        == derivatives[1].read_text(encoding="utf-8")
        == derivatives[2].read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "list_attributes",
    ("type=bulleted", "style=ordered", "ordered=true type=ordered"),
)
def test_naver_copy_normalizer_rejects_unknown_or_mixed_list_attributes(
    tmp_path: Path, list_attributes: str
) -> None:
    # Given: a LIST carries an unknown value, unknown key, or mixed dialects.
    source = f"""[TITLE]축제 안내[/TITLE]
[LIST {list_attributes}]
[ITEM]첫 항목[/ITEM]
[/LIST]
"""

    # When/Then: the host boundary rejects rather than guessing its semantics.
    with pytest.raises(ContractError, match="invalid tag attributes"):
        _ = normalize_naver_copy_text(source, tmp_path)


def test_naver_copy_normalizer_preserves_key_value_table_without_title(
    tmp_path: Path,
) -> None:
    source = """[TABLE]
[ROW]기간=2026년 10월 16일~18일[/ROW]
[ROW]장소=변산해수욕장[/ROW]
[/TABLE]
"""

    normalized = normalize_naver_copy_text(source, tmp_path)

    assert normalized == """[TABLE title=\"핵심 정보\"]
[ROW]기간=2026년 10월 16일~18일[/ROW]
[ROW]장소=변산해수욕장[/ROW]
[/TABLE]
"""


def _writer_stage_files(root: Path) -> None:
    _ = (root / "writer.md").write_text("instruction", encoding="utf-8")
    schema_dir = root / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )


def _topic_selector_stage_files(root: Path) -> None:
    _ = (root / "topic-selector.md").write_text("instruction", encoding="utf-8")
    schema_dir = root / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )


def _batch_selection_context(
    *,
    policy: Literal["capture_once", "reuse_only"],
    digest: str | None = None,
    exclusions: tuple[str, ...] = (),
    slot: int = 1,
) -> TopicSelectionContext:
    return TopicSelectionContext(
        category="",
        audience="",
        publish_purpose="",
        as_of_date="2026-09-07",
        batch_id="BATCH-test",
        batch_slot=slot,
        snapshot_policy=policy,
        capture_id="capture-test",
        snapshot_path="metadata/creator-advisor/2026-09-07/capture-test.json",
        snapshot_sha256=digest,
        excluded_keywords=exclusions,
    )


def _batch_stage_context(
    root: Path, selection: TopicSelectionContext
) -> StageExecutionContext:
    return StageExecutionContext(
        root=root,
        stage="topic-selector",
        run_id=f"RUN-slot-{selection.batch_slot}",
        topic_id=f"TOPIC-slot-{selection.batch_slot}",
        keyword=None,
        work_dir=root / ".automation" / f"slot-{selection.batch_slot}",
        selection_context=selection,
    )


def _snapshot(capture_id: str = "capture-test") -> CreatorAdvisorSnapshot:
    return CreatorAdvisorSnapshot(
        as_of_date="2026-09-07",
        captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=UTC).isoformat(),
        capture_id=capture_id,
        candidates=(
            CreatorAdvisorCandidate("가을 여행", 1),
            CreatorAdvisorCandidate("서울 축제", 2),
        ),
    )


def _selector_run(
    args: list[str],
    *,
    keyword: str,
    include_snapshot: bool,
    snapshot_payload: JSONMap | None = None,
) -> subprocess.CompletedProcess[str]:
    workspace_root = Path(args[args.index("--cd") + 1])
    declared = [f"research/topic-selection-{keyword}.md"]
    selection = workspace_root / "artifacts" / declared[0]
    selection.parent.mkdir(parents=True)
    _ = selection.write_text("selection", encoding="utf-8")
    if include_snapshot:
        relative = "metadata/creator-advisor/2026-09-07/capture-test.json"
        staged = workspace_root / "artifacts" / relative
        staged.parent.mkdir(parents=True)
        _ = staged.write_text(
            json.dumps(snapshot_payload or _snapshot().as_json(), ensure_ascii=False),
            encoding="utf-8",
        )
        declared.append(relative)
    output_path = Path(args[args.index("--output-last-message") + 1])
    _ = output_path.write_text(
        json.dumps(
            {
                "stage": "topic-selector",
                "status": "passed",
                "execution": "produced",
                "artifacts": declared,
                "resolved_keyword": keyword,
            }
        ),
        encoding="utf-8",
    )
    return subprocess.CompletedProcess(args, 0, "", "")


def test_auto_slot_one_captures_only_predeclared_snapshot_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: slot one has a capture-once context and no existing snapshot.
    _topic_selector_stage_files(tmp_path)
    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return _selector_run(args, keyword="가을 여행", include_snapshot=True)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    # When: the selector completes its first attempt.
    result = CodexStageExecutor().execute(
        _batch_stage_context(tmp_path, _batch_selection_context(policy="capture_once"))
    )

    # Then: the exact snapshot is promoted and trusted metadata is returned.
    assert result.artifacts == (
        "research/topic-selection-가을 여행.md",
        "metadata/creator-advisor/2026-09-07/capture-test.json",
    )
    assert result.details is not None
    assert result.details["capture_id"] == "capture-test"


def test_capture_once_host_rejects_untrusted_snapshot_identity_without_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the nested selector emits valid observations with hostile contract fields.
    _topic_selector_stage_files(tmp_path)
    payload = _snapshot().as_json()
    payload.update(
        {
            "schema_version": "wrong",
            "capture_id": "CAPTURE-hostile",
            "as_of_date": "1999-01-01",
            "timezone": "UTC",
            "source_url": "https://attacker.invalid",
        }
    )

    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return _selector_run(
            args,
            keyword="서울 축제",
            include_snapshot=True,
            snapshot_payload=payload,
        )

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    # When/Then: identity drift fails closed instead of rewriting raw evidence.
    with pytest.raises(ContractError, match="schema_version"):
        _ = CodexStageExecutor().execute(
            _batch_stage_context(
                tmp_path, _batch_selection_context(policy="capture_once")
            )
        )
    promoted = tmp_path / "metadata/creator-advisor/2026-09-07/capture-test.json"
    assert not promoted.exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"captured_at": None}, "captured_at"),
        ({"captured_at": "not-a-timestamp"}, "captured_at"),
        ({"candidates": None}, "candidates"),
        ({"candidates": []}, "candidates"),
        ({"candidates": [{"keyword": "서울 축제", "rank": True}]}, "rank"),
    ],
)
def test_capture_once_host_rejects_invalid_snapshot_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: JSONMap,
    message: str,
) -> None:
    # Given: the nested selector emits an invalid timestamp or candidate observation.
    _topic_selector_stage_files(tmp_path)
    payload = _snapshot().as_json()
    payload.update(mutation)

    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return _selector_run(
            args,
            keyword="서울 축제",
            include_snapshot=True,
            snapshot_payload=payload,
        )

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    # When/Then: trusted host canonicalization fails before artifact promotion.
    with pytest.raises(ContractError, match=message):
        _ = CodexStageExecutor().execute(
            _batch_stage_context(
                tmp_path, _batch_selection_context(policy="capture_once")
            )
        )


def test_capture_once_host_rejects_unreadable_snapshot_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the nested selector stages a non-JSON observation document.
    _topic_selector_stage_files(tmp_path)

    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        completed = _selector_run(args, keyword="서울 축제", include_snapshot=True)
        workspace_root = Path(args[args.index("--cd") + 1])
        staged = (
            workspace_root
            / "artifacts/metadata/creator-advisor/2026-09-07/capture-test.json"
        )
        _ = staged.write_text("not-json", encoding="utf-8")
        return completed

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    # When/Then: trusted host canonicalization fails before artifact promotion.
    with pytest.raises(ContractError, match="unreadable"):
        _ = CodexStageExecutor().execute(
            _batch_stage_context(
                tmp_path, _batch_selection_context(policy="capture_once")
            )
        )


def test_capture_once_prompt_supplies_canonical_snapshot_contract(
    tmp_path: Path,
) -> None:
    # Given: slot one must turn generic parent evidence into its declared snapshot.
    _topic_selector_stage_files(tmp_path)
    context = _batch_selection_context(policy="capture_once")

    # When: the nested selector prompt is built.
    prompt = stage_prompt(
        _batch_stage_context(tmp_path, context),
        tmp_path / "topic-selector.md",
        tmp_path / "result.json",
    )

    # Then: the embedded contract matches the canonical snapshot serializer shape.
    marker = "<creator-advisor-snapshot-contract>\n"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("\n</creator-advisor-snapshot-contract>", start)
    contract = json.loads(prompt[start:end])
    assert context.capture_id is not None
    canonical = CreatorAdvisorSnapshot(
        as_of_date=context.as_of_date,
        captured_at="<KST ISO-8601>",
        capture_id=context.capture_id,
        candidates=(CreatorAdvisorCandidate("<visible keyword>", 1),),
    ).as_json()
    assert contract == canonical


def test_auto_slot_one_retry_reuses_existing_snapshot_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: slot one's immutable snapshot already exists after an interrupted attempt.
    _topic_selector_stage_files(tmp_path)
    snapshot_path = write_snapshot(tmp_path, _snapshot())
    original = snapshot_path.read_bytes()
    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return _selector_run(args, keyword="가을 여행", include_snapshot=False)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    # When: capture-once is retried.
    result = CodexStageExecutor().execute(
        _batch_stage_context(tmp_path, _batch_selection_context(policy="capture_once"))
    )

    # Then: selection alone is new and the snapshot bytes remain unchanged.
    assert result.artifacts == ("research/topic-selection-가을 여행.md",)
    assert snapshot_path.read_bytes() == original


def test_program_commits_topic_decision_before_selector_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _topic_selector_stage_files(tmp_path)
    snapshot = CreatorAdvisorSnapshot(
        as_of_date="2026-09-07",
        captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=UTC).isoformat(),
        capture_id="capture-test",
        candidates=(
            CreatorAdvisorCandidate(
                "가을 여행",
                1,
                category_key="국내여행",
                category_rank=1,
                candidate_id="travel-1",
            ),
        ),
    )
    snapshot_path = write_snapshot(tmp_path, snapshot)
    context = _batch_selection_context(
        policy="reuse_only", digest=snapshot_sha256(snapshot_path)
    )

    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        decision_path = tmp_path / ".automation/slot-1/selection-decision.json"
        assert decision_path.is_file()
        prompt = args[-1]
        assert '<selection-decision>{' in prompt
        assert '"selected_keyword": "가을 여행"' in prompt
        assert "Do not choose, rank, replace, or add candidates" in prompt
        return _selector_run(args, keyword="가을 여행", include_snapshot=False)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    result = CodexStageExecutor().execute(_batch_stage_context(tmp_path, context))

    assert result.details is not None
    assert isinstance(result.details["selection_decision_digest"], str)


def test_auto_slot_two_reuses_digest_without_browser_or_metadata_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: slot two points to slot one's verified snapshot digest.
    _topic_selector_stage_files(tmp_path)
    snapshot_path = write_snapshot(tmp_path, _snapshot())
    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return _selector_run(args, keyword="서울 축제", include_snapshot=False)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)
    context = _batch_selection_context(
        policy="reuse_only",
        digest=snapshot_sha256(snapshot_path),
        exclusions=("가을 여행",),
        slot=2,
    )

    # When: slot two selects from the shared snapshot.
    result = CodexStageExecutor().execute(_batch_stage_context(tmp_path, context))

    # Then: no metadata output is accepted and the verified snapshot is reused.
    assert result.artifacts == ("research/topic-selection-서울 축제.md",)
    assert result.details is not None
    assert result.details["selection_snapshot_sha256"] == snapshot_sha256(snapshot_path)


@pytest.mark.parametrize(
    ("policy", "slot"),
    [("reuse_only", 2), ("capture_once", 1)],
)
def test_auto_slot_context_snapshot_precedes_environment_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: Literal["capture_once", "reuse_only"],
    slot: int,
) -> None:
    # Given: a verified context snapshot and generic parent evidence disagree.
    _topic_selector_stage_files(tmp_path)
    snapshot_path = write_snapshot(tmp_path, _snapshot())
    conflict = tmp_path / "evidence" / "other.json"
    conflict.parent.mkdir()
    _ = conflict.write_text('{"keyword":"wrong source"}', encoding="utf-8")
    monkeypatch.setenv("NAVER_STAGE_BROWSER_EVIDENCE", "evidence/other.json")
    prompts: list[str] = []

    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        prompts.append(args[-1])
        return _selector_run(args, keyword="서울 축제", include_snapshot=False)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)
    context = _batch_selection_context(
        policy=policy, digest=snapshot_sha256(snapshot_path), slot=slot
    )

    # When: the selector executes with both evidence sources present.
    result = CodexStageExecutor().execute(_batch_stage_context(tmp_path, context))

    # Then: only the explicit verified snapshot is handed to the nested stage.
    marker = "<browser-evidence>\n"
    start = prompts[0].index(marker) + len(marker)
    end = prompts[0].index("\n</browser-evidence>", start)
    assert json.loads(prompts[0][start:end]) == _snapshot().as_json()
    assert result.details is not None
    assert result.details["selection_snapshot_sha256"] == snapshot_sha256(snapshot_path)


def test_auto_slot_rejects_snapshot_identity_or_digest_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an explicit snapshot has a bad digest while generic evidence is available.
    _topic_selector_stage_files(tmp_path)
    _ = write_snapshot(tmp_path, _snapshot())
    generic = tmp_path / "evidence" / "parent.json"
    generic.parent.mkdir()
    _ = generic.write_text('{"keyword":"generic"}', encoding="utf-8")
    monkeypatch.setenv("NAVER_STAGE_BROWSER_EVIDENCE", "evidence/parent.json")
    context = _batch_selection_context(policy="reuse_only", digest="0" * 64, slot=2)

    # When/Then: generic evidence cannot replace the invalid explicit snapshot.
    with pytest.raises(ContractError, match="digest"):
        _ = CodexStageExecutor().execute(_batch_stage_context(tmp_path, context))


@pytest.mark.parametrize("mode", ["shadow", "active"])
def test_topic_selection_rejects_caller_fabricated_feedback_pins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["shadow", "active"],
) -> None:
    # Given: the caller fabricates feedback digests without trusted local evidence.
    _topic_selector_stage_files(tmp_path)
    snapshot = CreatorAdvisorSnapshot(
        as_of_date="2026-09-07",
        captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=UTC).isoformat(),
        capture_id="capture-test",
        candidates=(
            CreatorAdvisorCandidate("가을 여행", 1, channel_inflow=0.0),
            CreatorAdvisorCandidate("서울 축제", 2, channel_inflow=100.0),
        ),
    )
    snapshot_path = write_snapshot(tmp_path, snapshot)
    context = replace(
        _batch_selection_context(
            policy="reuse_only", digest=snapshot_sha256(snapshot_path)
        ),
        score_version=(
            "topic-feedback-v1" if mode == "active" else "topic-baseline-v1"
        ),
        score_config_digest="sha256:" + "1" * 64,
        feedback_manifest_digest="sha256:" + "2" * 64,
        feedback_selection_mode=mode,
    )

    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return _selector_run(args, keyword="가을 여행", include_snapshot=False)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    # When/Then: the real stage boundary reconstructs trust and rejects the pins.
    with pytest.raises(ContractError, match="pins changed"):
        _ = CodexStageExecutor().execute(_batch_stage_context(tmp_path, context))


@pytest.mark.parametrize(
    ("keyword", "exclusions", "message"),
    [
        ("없는 후보", (), "absent"),
        ("가을 여행", ("  가을   여행 ",), "excluded"),
    ],
)
def test_auto_slot_rejects_keyword_absent_from_snapshot_or_in_exclusions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keyword: str,
    exclusions: tuple[str, ...],
    message: str,
) -> None:
    # Given: a reuse selector returns a non-candidate or normalized duplicate.
    _topic_selector_stage_files(tmp_path)
    snapshot_path = write_snapshot(tmp_path, _snapshot())

    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        return _selector_run(args, keyword=keyword, include_snapshot=False)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)
    context = _batch_selection_context(
        policy="reuse_only",
        digest=snapshot_sha256(snapshot_path),
        exclusions=exclusions,
        slot=2,
    )

    # When/Then: trusted candidate membership and distinctness fail closed.
    with pytest.raises(ContractError, match=message):
        _ = CodexStageExecutor().execute(_batch_stage_context(tmp_path, context))


def _successful_writer_run(
    args: list[str], **_kwargs: SubprocessValue
) -> subprocess.CompletedProcess[str]:
    stage_cwd = Path(args[args.index("--cd") + 1])
    artifact = stage_cwd / "artifacts" / "drafts" / "test.md"
    artifact.parent.mkdir(parents=True)
    _ = artifact.write_text("draft", encoding="utf-8")
    output_path = Path(args[args.index("--output-last-message") + 1])
    _ = output_path.write_text(
        json.dumps(
            {
                "stage": "writer",
                "status": "passed",
                "execution": "produced",
                "artifacts": ["drafts/test.md"],
            }
        ),
        encoding="utf-8",
    )
    return subprocess.CompletedProcess(args, 0, "", "")


def test_stage_executor_omits_browser_evidence_tag_when_not_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _writer_stage_files(tmp_path)
    monkeypatch.delenv("NAVER_STAGE_BROWSER_EVIDENCE", raising=False)
    prompts: list[str] = []

    def capture_run(
        args: list[str], **kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        prompts.append(args[-1])
        return _successful_writer_run(args, **kwargs)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    _ = CodexStageExecutor().execute(_stage_context(tmp_path))

    assert len(prompts) == 1
    assert "<browser-evidence>" not in prompts[0]


def test_image_stage_does_not_reuse_partial_existing_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed stage may leave these files behind; their presence alone is not proof of success.
    _ = (tmp_path / "image-maker.md").write_text("instruction", encoding="utf-8")
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    _ = (schema_dir / "stage-result.schema.json").write_text(
        (Path(__file__).parents[1] / "schemas" / "stage-result.schema.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    asset_dir = tmp_path / "assets" / "test"
    asset_dir.mkdir(parents=True)
    _ = (asset_dir / "image-map.md").write_text("partial", encoding="utf-8")
    _ = (asset_dir / "thumbnail.png").write_bytes(b"partial image")
    _ = (asset_dir / "image-01.png").write_bytes(b"partial image")
    calls: list[str] = []

    def fail_stage(*_args: object, **_kwargs: object) -> None:
        calls.append("image-maker")
        raise StageExecutionError("image-maker", "expected fixture failure")

    monkeypatch.setattr("tools.codex_stage_executor.run_codex", fail_stage)

    with pytest.raises(StageExecutionError, match="expected fixture failure"):
        _ = CodexStageExecutor().execute(_stage_context(tmp_path, stage="image-maker"))

    assert calls == ["image-maker"]


@pytest.mark.parametrize(
    ("stage", "keyword", "expected_paths"),
    [
        (
            "topic-selector",
            "2026 서울세계불꽃축제 교통통제",
            ("research/topic-selection-2026 서울세계불꽃축제 교통통제.md",),
        ),
        (
            "researcher",
            "2026 서울세계불꽃축제 교통통제",
            ("research/2026 서울세계불꽃축제 교통통제.md",),
        ),
        (
            "writer",
            "2026 서울세계불꽃축제 교통통제",
            ("drafts/2026 서울세계불꽃축제 교통통제.md",),
        ),
        (
            "image-maker",
            "2026 서울세계불꽃축제 교통통제",
            ("assets/2026 서울세계불꽃축제 교통통제/",),
        ),
        (
            "content-assembler",
            "2026 서울세계불꽃축제 교통통제",
            (
                "final/2026 서울세계불꽃축제 교통통제.md",
                "final/2026 서울세계불꽃축제 교통통제-naver-layout.md",
                "final/2026 서울세계불꽃축제 교통통제-naver-copy.md",
                "final/2026 서울세계불꽃축제 교통통제-naver-input.md",
            ),
        ),
    ],
)
def test_stage_prompt_pins_literal_canonical_producer_paths(
    tmp_path: Path, stage: str, keyword: str, expected_paths: tuple[str, ...]
) -> None:
    context = StageExecutionContext(
        root=tmp_path,
        stage=stage,
        run_id="RUN-test",
        topic_id="TOPIC-test",
        keyword=keyword,
        work_dir=tmp_path / ".automation" / "work",
    )

    prompt = stage_prompt(context, tmp_path / f"{stage}.md", tmp_path / "result.json")

    for expected_path in expected_paths:
        assert expected_path in prompt


def test_content_assembler_prompt_disambiguates_staging_artifact_prefix(
    tmp_path: Path,
) -> None:
    context = StageExecutionContext(
        root=tmp_path,
        stage="content-assembler",
        run_id="RUN-test",
        topic_id="TOPIC-test",
        keyword="kfc 1+1",
        work_dir=tmp_path / ".automation" / "work",
    )

    prompt = stage_prompt(
        context, tmp_path / "content-assembler.md", tmp_path / "result.json"
    )

    assert "artifacts/final/kfc 1+1-naver-layout.md" in prompt
    assert "never write to final/ directly" in prompt


def test_researcher_prompt_targets_a_run_scoped_revision_when_requested(
    tmp_path: Path,
) -> None:
    context = StageExecutionContext(
        root=tmp_path,
        stage="researcher",
        run_id="RUN-current",
        topic_id="TOPIC-test",
        keyword="축제 주제",
        work_dir=tmp_path / ".automation" / "work",
    )

    prompt = stage_prompt(
        context,
        tmp_path / "researcher.md",
        tmp_path / "result.json",
        research_artifact_path="research/revisions/RUN-current/축제 주제.md",
    )

    assert "Canonical output path: research/revisions/RUN-current/축제 주제.md" in prompt
    assert "artifacts/research/revisions/RUN-current/축제 주제.md" in prompt


def test_content_assembler_prompt_limits_canonical_image_attributes(
    tmp_path: Path,
) -> None:
    context = StageExecutionContext(
        root=tmp_path,
        stage="content-assembler",
        run_id="run-image-attrs",
        topic_id="topic-kfc",
        keyword="kfc 1+1",
        work_dir=tmp_path / ".automation" / "work",
    )

    prompt = stage_prompt(
        context,
        tmp_path / "content-assembler.md",
        tmp_path / "stage-result.jsonl",
    )

    assert "IMAGE tags may contain only file, alt, and representative" in prompt


def test_topic_selector_prompt_includes_canonical_resolved_selection_path(
    tmp_path: Path,
) -> None:
    context = StageExecutionContext(
        root=tmp_path,
        stage="topic-selector",
        run_id="RUN-test",
        topic_id="TOPIC-test",
        keyword=None,
        work_dir=tmp_path / ".automation" / "work",
    )

    prompt = stage_prompt(
        context, tmp_path / "topic-selector.md", tmp_path / "result.json"
    )

    assert "research/topic-selection-<resolved keyword>.md" in prompt


def test_auto_topic_selector_uses_host_managed_aside_lifecycle(
    tmp_path: Path,
) -> None:
    # Given: an automatic selector is launched as a nested stage.
    context = StageExecutionContext(
        root=tmp_path,
        stage="topic-selector",
        run_id="RUN-test",
        topic_id="TOPIC-test",
        keyword=None,
        work_dir=tmp_path / ".automation" / "work",
    )

    # When: the nested stage prompt is built.
    prompt = stage_prompt(
        context, tmp_path / "topic-selector.md", tmp_path / "result.json"
    )

    # Then: its structural Aside policy forbids another update and reuses the session.
    start = prompt.index("<aside-preflight")
    end = prompt.index("/>", start) + 2
    policy = ElementTree.fromstring(prompt[start:end])
    assert policy.attrib == {
        "guide": "host-managed",
        "session": "host-capture",
        "update": "host-managed",
    }


def test_user_defined_topic_selector_omits_aside_preflight_tag(
    tmp_path: Path,
) -> None:
    context = StageExecutionContext(
        root=tmp_path,
        stage="topic-selector",
        run_id="RUN-test",
        topic_id="TOPIC-test",
        keyword="청년미래적금",
        work_dir=tmp_path / ".automation" / "work",
    )

    prompt = stage_prompt(
        context, tmp_path / "topic-selector.md", tmp_path / "result.json"
    )

    assert "<aside-preflight" not in prompt


def test_stage_executor_includes_untrusted_browser_evidence_in_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _writer_stage_files(tmp_path)
    evidence_path = tmp_path / "evidence" / "browser.txt"
    evidence_path.parent.mkdir()
    _ = evidence_path.write_text(
        "https://example.test/page\nObserved fact", encoding="utf-8"
    )
    monkeypatch.setenv("NAVER_STAGE_BROWSER_EVIDENCE", "evidence/browser.txt")
    prompts: list[str] = []
    environments: list[dict[str, str]] = []

    def capture_run(
        args: list[str], **kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        prompts.append(args[-1])
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        environments.append(environment)
        return _successful_writer_run(args, **kwargs)

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)

    _ = CodexStageExecutor().execute(_stage_context(tmp_path))

    marker = "<browser-evidence>\n"
    start = prompts[0].index(marker) + len(marker)
    end = prompts[0].index("\n</browser-evidence>", start)
    assert prompts[0][start:end] == evidence_path.read_text(encoding="utf-8")
    assert "NAVER_STAGE_BROWSER_EVIDENCE" not in environments[0]


def test_stage_executor_reuses_supplied_browser_evidence_as_immutable_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an append-only Creator Advisor snapshot already exists in the project.
    _topic_selector_stage_files(tmp_path)
    relative_evidence = "metadata/creator-advisor/2026-09-04/capture.json"
    evidence_path = tmp_path / relative_evidence
    evidence_path.parent.mkdir(parents=True)
    original = b'{"keyword":"fresh topic"}'
    _ = evidence_path.write_bytes(original)
    monkeypatch.setenv("NAVER_STAGE_BROWSER_EVIDENCE", relative_evidence)
    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        workspace_root = Path(args[args.index("--cd") + 1])
        staged_evidence = workspace_root / "artifacts" / relative_evidence
        staged_evidence.parent.mkdir(parents=True)
        _ = staged_evidence.write_bytes(original)
        selection = (
            workspace_root / "artifacts" / "research" / "topic-selection-fresh topic.md"
        )
        selection.parent.mkdir(parents=True)
        _ = selection.write_text("selection", encoding="utf-8")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "topic-selector",
                    "status": "passed",
                    "execution": "produced",
                    "artifacts": [
                        relative_evidence,
                        "research/topic-selection-fresh topic.md",
                    ],
                    "resolved_keyword": "fresh topic",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)
    context = StageExecutionContext(
        root=tmp_path,
        stage="topic-selector",
        run_id="RUN-new",
        topic_id="TOPIC-new",
        keyword=None,
        work_dir=tmp_path / ".automation" / "work",
    )

    # When: the stage redundantly returns the supplied evidence with its new selection.
    result = CodexStageExecutor().execute(context)

    # Then: the evidence remains input-only and only the new selection is promoted.
    assert result.artifacts == ("research/topic-selection-fresh topic.md",)
    assert evidence_path.read_bytes() == original


def test_stage_executor_rejects_changed_copy_of_supplied_browser_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a stage attempts to return altered bytes for supplied evidence.
    _topic_selector_stage_files(tmp_path)
    relative_evidence = "metadata/creator-advisor/2026-09-04/capture.json"
    evidence_path = tmp_path / relative_evidence
    evidence_path.parent.mkdir(parents=True)
    _ = evidence_path.write_bytes(b"original")
    monkeypatch.setenv("NAVER_STAGE_BROWSER_EVIDENCE", relative_evidence)

    def capture_run(
        args: list[str], **_kwargs: SubprocessValue
    ) -> subprocess.CompletedProcess[str]:
        workspace_root = Path(args[args.index("--cd") + 1])
        staged_evidence = workspace_root / "artifacts" / relative_evidence
        staged_evidence.parent.mkdir(parents=True)
        _ = staged_evidence.write_bytes(b"changed")
        output_path = Path(args[args.index("--output-last-message") + 1])
        _ = output_path.write_text(
            json.dumps(
                {
                    "stage": "topic-selector",
                    "status": "passed",
                    "execution": "produced",
                    "artifacts": [relative_evidence],
                    "resolved_keyword": "fresh topic",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", capture_run)
    context = StageExecutionContext(
        root=tmp_path,
        stage="topic-selector",
        run_id="RUN-new",
        topic_id="TOPIC-new",
        keyword=None,
        work_dir=tmp_path / ".automation" / "work",
    )

    # When/Then: immutable input enforcement rejects the changed copy.
    with pytest.raises(ContractError, match="supplied browser evidence was changed"):
        _ = CodexStageExecutor().execute(context)
    assert evidence_path.read_bytes() == b"original"


@pytest.mark.parametrize(
    ("evidence_value", "contents"),
    [
        ("/tmp/browser-evidence.txt", None),
        ("../browser-evidence.txt", None),
        ("missing.txt", None),
        ("empty.txt", b""),
        ("invalid.txt", b"\x80"),
    ],
)
def test_stage_executor_rejects_invalid_browser_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence_value: str,
    contents: bytes | None,
) -> None:
    _writer_stage_files(tmp_path)
    if contents is not None:
        _ = (tmp_path / evidence_value).write_bytes(contents)
    monkeypatch.setenv("NAVER_STAGE_BROWSER_EVIDENCE", evidence_value)

    with pytest.raises(ContractError, match="browser evidence"):
        _ = CodexStageExecutor().execute(_stage_context(tmp_path))
