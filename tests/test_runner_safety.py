from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import assert_never

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import ExternalWriteRequest
from tools.runner_execution import run_job
from tools.runner_job import validate_job_request
from tools.runner_types import (
    JobName,
    RunnerRequest,
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
    TopicSelectionContext,
)

NOW = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)


class ProducerStage(StrEnum):
    TOPIC_SELECTOR = "topic-selector"
    RESEARCHER = "researcher"
    WRITER = "writer"
    IMAGE_MAKER = "image-maker"
    CONTENT_ASSEMBLER = "content-assembler"


def _producer_stage(stage: str) -> ProducerStage:
    try:
        return ProducerStage(stage)
    except ValueError as error:
        raise AssertionError(f"unexpected producer stage: {stage}") from error


class UnsafeProducerExecutor:
    def __init__(self, run_status: RunStatus) -> None:
        self.run_status: RunStatus = run_status

    def execute(self, context: StageExecutionContext) -> StageResult:
        root = context.root
        keyword = context.keyword or "fixture"
        asset_dir = root / "assets" / keyword
        final_dir = root / "final"
        asset_dir.mkdir(parents=True, exist_ok=True)
        final_dir.mkdir(exist_ok=True)
        stage = _producer_stage(context.stage)
        match stage:
            case ProducerStage.TOPIC_SELECTOR:
                path = root / "research" / f"topic-selection-{keyword}.md"
                path.parent.mkdir(exist_ok=True)
                _ = path.write_text("# selection\n", encoding="utf-8")
                return StageResult(
                    RunStatus.PASSED,
                    StageExecution.PRODUCED,
                    artifacts=(path.relative_to(root).as_posix(),),
                    resolved_keyword=keyword,
                )
            case ProducerStage.RESEARCHER:
                path = root / "research" / f"{keyword}.md"
                _ = path.write_text("# research\n", encoding="utf-8")
                artifacts = (path.relative_to(root).as_posix(),)
            case ProducerStage.WRITER:
                path = root / "drafts" / f"{keyword}.md"
                path.parent.mkdir(exist_ok=True)
                _ = path.write_text("# draft\n", encoding="utf-8")
                artifacts = (path.relative_to(root).as_posix(),)
                return StageResult(
                    RunStatus.PASSED,
                    StageExecution.PRODUCED,
                    artifacts=artifacts,
                    run_status=self.run_status,
                )
            case ProducerStage.IMAGE_MAKER:
                for name, data in (("body.png", b"body"), ("thumbnail.png", b"thumb")):
                    _ = (asset_dir / name).write_bytes(data)
                _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
                artifacts = tuple(
                    f"assets/{keyword}/{name}"
                    for name in ("body.png", "thumbnail.png", "image-map.md")
                )
            case ProducerStage.CONTENT_ASSEMBLER:
                _ = (final_dir / f"{keyword}.md").write_text(
                    f"# {keyword}\n\n![body](../assets/{keyword}/body.png)\n",
                    encoding="utf-8",
                )
                for suffix in (
                    "-naver-layout.md",
                    "-naver-copy.md",
                    "-naver-input.md",
                ):
                    _ = (final_dir / f"{keyword}{suffix}").write_text(
                        f"# {keyword}\n", encoding="utf-8"
                    )
                artifacts = tuple(
                    f"final/{keyword}{suffix}"
                    for suffix in (
                        ".md",
                        "-naver-layout.md",
                        "-naver-copy.md",
                        "-naver-input.md",
                    )
                )
            case _:
                assert_never(stage)
        return StageResult(
            RunStatus.PASSED, StageExecution.PRODUCED, artifacts=artifacts
        )


class CountingNotion:
    def __init__(self) -> None:
        self.calls: int = 0

    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        self.calls += 1
        raise AssertionError(f"unexpected external write: {request}")


class CountingNaver:
    def __init__(self) -> None:
        self.prepare_calls: int = 0
        self.save_calls: int = 0

    @property
    def target_blog_id(self) -> str:
        return "blog-fixture"

    def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
        _ = (title, body, artifact_digest)
        self.prepare_calls += 1
        raise AssertionError("unexpected Naver preparation")

    def save(self, title: str, artifact_digest: str) -> JSONMap:
        _ = (title, artifact_digest)
        self.save_calls += 1
        raise AssertionError("unexpected Naver draft save")


@pytest.mark.parametrize(
    "run_status",
    [
        RunStatus.READY_FOR_NAVER,
        RunStatus.AWAITING_USER_CONFIRMATION,
        RunStatus.DRAFT_SAVED,
    ],
)
def test_producer_terminal_run_status_fails_without_promoting_global_status(
    tmp_path: Path,
    run_status: RunStatus,
) -> None:
    result = run_job(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            auto_topic=True,
            now=NOW,
            executor=UnsafeProducerExecutor(run_status),
            selection_context=TopicSelectionContext("", "", "", "2026-08-29"),
        )
    )

    assert result.status is RunStatus.FAILED
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "writer cannot set run_status" in result.message


def test_daily_generate_rejects_naver_without_verified_notion_before_execution(
    tmp_path: Path,
) -> None:
    naver = CountingNaver()

    with pytest.raises(ContractError, match="requires a Notion adapter"):
        _ = run_job(
            RunnerRequest(
                root=tmp_path,
                job="daily-generate",
                auto_topic=True,
                now=NOW,
                executor=UnsafeProducerExecutor(RunStatus.DRAFT_SAVED),
                naver_adapter=naver,
            )
        )

    assert not (tmp_path / ".automation").exists()
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0


def test_daily_generate_accepts_notion_without_naver_draft_adapter(
    tmp_path: Path,
) -> None:
    notion = CountingNotion()

    job = validate_job_request(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            auto_topic=True,
            now=NOW,
            notion_adapter=notion,
            selection_context=TopicSelectionContext("", "", "", "2026-08-29"),
        )
    )

    assert job is JobName.DAILY_GENERATE
    assert notion.calls == 0
