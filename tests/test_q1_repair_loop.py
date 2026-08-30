from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import ExternalWriteRequest
from tools.runner_execution import run_job
from tools.runner_types import (
    RunnerRequest,
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
)

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)


class Q1RepairExecutor:
    def __init__(self, failures: int, failure_message: str | None = None) -> None:
        self.failures: int = failures
        self.failure_message: str = failure_message or (
            "Q1 quality failure: missing reader answer"
        )
        self.calls: list[str] = []
        self.feedback: list[str | None] = []

    def execute(self, context: StageExecutionContext) -> StageResult:
        root = context.root
        keyword = context.keyword or "fixture"
        self.calls.append(context.stage)
        if context.stage == "topic-selector":
            path = root / "research" / f"topic-selection-{keyword}.md"
            path.parent.mkdir(exist_ok=True)
            _ = path.write_text("# selection\n", encoding="utf-8")
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                artifacts=(path.relative_to(root).as_posix(),),
                resolved_keyword=keyword,
            )
        if context.stage == "researcher":
            path = root / "research" / f"{keyword}.md"
            _ = path.write_text("# research\n", encoding="utf-8")
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                artifacts=(path.relative_to(root).as_posix(),),
            )
        if context.stage == "writer":
            path = root / "drafts" / f"{keyword}.md"
            path.parent.mkdir(exist_ok=True)
            _ = path.write_text("# draft\n", encoding="utf-8")
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                artifacts=(path.relative_to(root).as_posix(),),
            )
        if context.stage == "image-maker":
            directory = root / "assets" / keyword
            directory.mkdir(parents=True, exist_ok=True)
            _ = (directory / "body.png").write_bytes(b"body")
            _ = (directory / "thumbnail.png").write_bytes(b"thumbnail")
            _ = (directory / "image-map.md").write_text("# map\n", encoding="utf-8")
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                artifacts=tuple(
                    f"assets/{keyword}/{name}"
                    for name in ("body.png", "thumbnail.png", "image-map.md")
                ),
            )
        if context.stage != "content-assembler":
            raise AssertionError(f"unexpected stage: {context.stage}")
        self.feedback.append(context.q1_feedback)
        if len(self.feedback) <= self.failures:
            raise ContractError(self.failure_message)
        directory = root / "final"
        directory.mkdir(exist_ok=True)
        _ = (directory / f"{keyword}.md").write_text(
            f"# {keyword}\n\n![body](../assets/{keyword}/body.png)\n",
            encoding="utf-8",
        )
        for suffix in ("-naver-layout.md", "-naver-copy.md", "-naver-input.md"):
            _ = (directory / f"{keyword}{suffix}").write_text(
                f"# {keyword}\n", encoding="utf-8"
            )
        return StageResult(
            RunStatus.PASSED,
            StageExecution.PRODUCED,
            artifacts=tuple(
                f"final/{keyword}{suffix}"
                for suffix in (
                    ".md",
                    "-naver-layout.md",
                    "-naver-copy.md",
                    "-naver-input.md",
                )
            ),
        )


class CountingQ1Notion:
    def __init__(self) -> None:
        self.calls: int = 0

    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        from tools.manifest import verify_manifest

        self.calls += 1
        digest = verify_manifest(request.root, request.manifest_path).artifact_digest
        return {
            "notion_page_id": "page-fixture",
            "notion_last_verified_at": NOW.isoformat(),
            "notion_roundtrip_digest": digest,
        }


class FixtureNaver:
    def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
        _ = body
        return {
            "target_blog_id": "blog-fixture",
            "naver_title": title,
            "artifact_digest": artifact_digest,
        }

    def save(self, title: str, artifact_digest: str) -> JSONMap:
        _ = artifact_digest
        return {"draft_status": "saved", "naver_title": title}


def _request(
    root: Path,
    executor: Q1RepairExecutor,
    notion: CountingQ1Notion | None = None,
) -> RunnerRequest:
    return RunnerRequest(
        root=root,
        job="daily-generate",
        auto_topic=True,
        now=NOW,
        executor=executor,
        notion_adapter=notion,
        naver_adapter=FixtureNaver() if notion is not None else None,
    )


def _write_notion_config(root: Path) -> None:
    _ = (root / "notion-config.md").write_text(
        "- 데이터 소스 ID: " + chr(96) + "datasource-fixture" + chr(96) + "\n",
        encoding="utf-8",
    )


def test_q1_failure_retries_content_assembler_with_feedback_and_preserves_producers(
    tmp_path: Path,
) -> None:
    executor = Q1RepairExecutor(failures=1)
    notion = CountingQ1Notion()
    _write_notion_config(tmp_path)

    result = run_job(_request(tmp_path, executor, notion))

    assert result.status is RunStatus.AWAITING_USER_CONFIRMATION
    assert executor.calls == [
        "topic-selector",
        "researcher",
        "writer",
        "image-maker",
        "content-assembler",
        "content-assembler",
    ]
    assert executor.feedback == [None, "Q1 quality failure: missing reader answer"]
    assert notion.calls == 1
    events = [
        json.loads(line)
        for line in result.log_path.read_text(encoding="utf-8").splitlines()
    ]
    content_events = [event for event in events if event["stage"] == "content-assembler"]
    assert {event["run_id"] for event in content_events} == {result.run_id}
    assert [(event["attempt"], event["status"]) for event in content_events] == [
        (1, "failed"),
        (2, "passed"),
    ]


def test_q1_failure_stops_after_three_identical_content_attempts_without_notion(
    tmp_path: Path,
) -> None:
    executor = Q1RepairExecutor(failures=3)
    notion = CountingQ1Notion()
    _write_notion_config(tmp_path)

    result = run_job(_request(tmp_path, executor, notion))

    assert result.status is RunStatus.FAILED
    assert executor.calls == [
        "topic-selector",
        "researcher",
        "writer",
        "image-maker",
        "content-assembler",
        "content-assembler",
        "content-assembler",
    ]
    assert executor.feedback == [
        None,
        "Q1 quality failure: missing reader answer",
        "Q1 quality failure: missing reader answer",
    ]
    assert notion.calls == 0
    assert "Q1 failed after 3 attempts" in result.message
    events = [
        json.loads(line)
        for line in result.log_path.read_text(encoding="utf-8").splitlines()
    ]
    content_events = [event for event in events if event["stage"] == "content-assembler"]
    assert [(event["attempt"], event["status"]) for event in content_events] == [
        (1, "failed"),
        (2, "failed"),
        (3, "failed"),
    ]


def test_q1_feedback_and_log_redact_sensitive_failure_values(tmp_path: Path) -> None:
    executor = Q1RepairExecutor(
        failures=1,
        failure_message="Q1 quality failure: api_key=super-secret-value",
    )

    result = run_job(_request(tmp_path, executor))

    assert result.status is RunStatus.LOCAL_ONLY
    assert executor.feedback == [None, "Q1 quality failure: api_key=[redacted]"]
    assert "super-secret-value" not in result.log_path.read_text(encoding="utf-8")
