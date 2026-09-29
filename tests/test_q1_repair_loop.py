from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override

import pytest

from tests.article_quality_fixtures import install_passing_quality_review
from tools.codex_process import CodexProcessError, run_codex
from tools.codex_stage_error import StageExecutionError, StageFailureType
from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import ExternalWriteRequest
from tools.run_cancellation import create_cancellation
from tools.runner_execution import recover_job, resume_job, run_job
from tools.runner_types import (
    RunnerRequest,
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
    TopicSelectionContext,
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
                (
                    f"[TITLE] {keyword} [/TITLE]\n"
                    '[IMAGE file="thumbnail.png" alt="thumbnail" representative=true]\n'
                    '[IMAGE file="body.png" alt="body" representative=false]\n'
                ),
                encoding="utf-8",
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


class ClassifiedQ1Executor(Q1RepairExecutor):
    def __init__(self, failure: StageExecutionError) -> None:
        super().__init__(failures=0)
        self.failure: StageExecutionError = failure

    @override
    def execute(self, context: StageExecutionContext) -> StageResult:
        if context.stage != "content-assembler":
            return super().execute(context)
        self.calls.append(context.stage)
        self.feedback.append(context.q1_feedback)
        raise self.failure


class ProcessQ1Executor(Q1RepairExecutor):
    def __init__(self, work_dir: Path) -> None:
        super().__init__(failures=0)
        self.work_dir: Path = work_dir

    @override
    def execute(self, context: StageExecutionContext) -> StageResult:
        if context.stage != "content-assembler":
            return super().execute(context)
        self.calls.append(context.stage)
        self.feedback.append(context.q1_feedback)
        try:
            run_codex(
                ["codex"],
                root=context.root,
                environment={},
                timeout=1,
                work_dir=self.work_dir,
                stage_attempt=context.stage_attempt,
            )
        except CodexProcessError as error:
            raise StageExecutionError(
                context.stage,
                str(error),
                error.error_type,
                error.retryable,
                error.next_action,
                error.attempts,
            ) from error
        raise AssertionError("process fixture must fail")


class CountingQ1Notion:
    def __init__(self) -> None:
        self.calls: int = 0

    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        from tools.manifest import verify_manifest

        self.calls += 1
        manifest = verify_manifest(request.root, request.manifest_path)
        digest = manifest.artifact_digest
        _ = install_passing_quality_review(
            request.root,
            run_id=request.run_id,
            topic_id=manifest.topic_id,
            artifact_digest=digest,
            manifest={"files": [entry.as_json() for entry in manifest.files]},
            reviewed_at=(NOW + timedelta(seconds=1)).isoformat(),
        )
        content_digest = "sha256:" + "1" * 64
        return {
            "storage_integrity": "passed",
            "notion_page_id": "page-fixture",
            "notion_last_verified_at": NOW.isoformat(),
            "expected_notion_content_digest": content_digest,
            "notion_content_digest": content_digest,
            "notion_roundtrip_digest": content_digest,
            "artifact_digest": digest,
        }


class FixtureNaver:
    @property
    def target_blog_id(self) -> str:
        return "blog-fixture"

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
        selection_context=TopicSelectionContext("", "", "", "2026-08-30"),
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

    request = _request(tmp_path, executor, notion)
    result = run_job(request)
    if result.status is RunStatus.READY_FOR_NAVER:
        result = resume_job(replace(request, run_id=result.run_id, resume=True))

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
    content_events = [
        event for event in events if event["stage"] == "content-assembler"
    ]
    assert {event["run_id"] for event in content_events} == {result.run_id}
    assert [(event["attempt"], event["status"]) for event in content_events] == [
        (1, "failed"),
        (2, "passed"),
    ]
    assert content_events[0]["error_type"] == "q1_contract_failure"
    assert content_events[1]["error_type"] is None


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
    content_events = [
        event for event in events if event["stage"] == "content-assembler"
    ]
    assert [(event["attempt"], event["status"]) for event in content_events] == [
        (1, "failed"),
        (2, "failed"),
        (3, "failed"),
    ]


def test_q1_repair_does_not_start_another_attempt_after_cancellation(
    tmp_path: Path,
) -> None:
    class CancelDuringFirstRepair(Q1RepairExecutor):
        @override
        def execute(self, context: StageExecutionContext) -> StageResult:
            if context.stage == "content-assembler" and not self.feedback:
                self.calls.append(context.stage)
                self.feedback.append(context.q1_feedback)
                _ = create_cancellation(
                    context.root,
                    run_id=context.run_id,
                    batch_id="BATCH-fixture",
                    child_id="CHILD-fixture",
                    scope="remaining",
                    nonce="fixture-cancel-nonce",
                )
                raise ContractError("Q1 quality failure: missing reader answer")
            return super().execute(context)

    executor = CancelDuringFirstRepair(failures=0)

    result = run_job(_request(tmp_path, executor))

    assert result.status is RunStatus.CANCELLED
    assert executor.calls.count("content-assembler") == 1, (
        "a persisted cancellation must stop the Q1 repair loop before another paid attempt"
    )


def test_q1_feedback_and_log_redact_sensitive_failure_values(tmp_path: Path) -> None:
    executor = Q1RepairExecutor(
        failures=1,
        failure_message="Q1 quality failure: api_key=super-secret-value",
    )

    result = run_job(_request(tmp_path, executor))

    assert result.status is RunStatus.LOCAL_ONLY
    assert executor.feedback == [None, "Q1 quality failure: api_key=[redacted]"]
    assert "super-secret-value" not in result.log_path.read_text(encoding="utf-8")


def test_q1_retry_budget_is_cumulative_when_a_failed_run_is_resumed(
    tmp_path: Path,
) -> None:
    first_executor = Q1RepairExecutor(failures=3)
    first = run_job(_request(tmp_path, first_executor))
    assert first.status is RunStatus.FAILED

    resumed_executor = Q1RepairExecutor(failures=0)

    resumed = recover_job(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            run_id=first.run_id,
            now=NOW,
            executor=resumed_executor,
        )
    )

    assert resumed.status is RunStatus.FAILED
    assert resumed_executor.calls == []
    assert "retry limit" in resumed.message.lower()
    content_events = [
        json.loads(line)
        for line in first.log_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("stage") == "content-assembler"
    ]
    assert len(content_events) == 3


def test_exhausted_process_rate_limit_does_not_start_a_q1_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the process transport owns and exhausts its three 429 attempts.
    calls = 0

    def rate_limited(
        args: list[str], **_kwargs: str | Path | list[str] | int | bool | None
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(args, 1, "429 Too Many Requests", "")

    monkeypatch.setattr("tools.codex_process.subprocess.run", rate_limited)
    def no_sleep(_delay: float) -> None:
        return

    monkeypatch.setattr("tools.codex_process.time.sleep", no_sleep)
    executor = ProcessQ1Executor(tmp_path / "process-work")

    # When: the full runner reaches content assembly.
    result = run_job(_request(tmp_path, executor))

    # Then: runner does not multiply the exhausted transport budget.
    assert result.status is RunStatus.FAILED
    assert calls == 3
    assert executor.feedback == [None]
    assert "Q1 failed after" not in result.message


def test_q1_input_unrepairable_stops_without_recalling_content_assembler(
    tmp_path: Path,
) -> None:
    # Given: a deterministic check assigns the defect to an upstream input.
    executor = ClassifiedQ1Executor(
        StageExecutionError(
            "content-assembler",
            "draft lacks required evidence",
            "q1_input_unrepairable",
        )
    )

    # When: content assembly reports that structured failure.
    result = run_job(_request(tmp_path, executor))

    # Then: no producer is recalled and the failure remains a failure.
    assert result.status is RunStatus.FAILED
    assert executor.feedback == [None]
    assert executor.calls.count("content-assembler") == 1
    assert "Q1 failed after" not in result.message


def test_normal_process_contract_failure_remains_a_bounded_q1_quality_repair(
    tmp_path: Path,
) -> None:
    # Given: the process completed, then output validation rejected the assembly.
    executor = ClassifiedQ1Executor(
        StageExecutionError(
            "content-assembler",
            "structured output violates the assembly contract",
            StageFailureType.CONTRACT_FAILED.value,
            process_attempts=0,
        )
    )

    # When: the runner applies the Q1 repair policy.
    result = run_job(_request(tmp_path, executor))

    # Then: the quality loop stays bounded at three assembler calls.
    assert result.status is RunStatus.FAILED
    assert executor.calls.count("content-assembler") == 3
    assert "Q1 failed after 3 attempts" in result.message
    events = [
        json.loads(line)
        for line in result.log_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("stage") == "content-assembler"
    ]
    assert {event["error_type"] for event in events} == {"q1_contract_failure"}


def test_q1_temporary_io_retries_once_but_configuration_failure_does_not(
    tmp_path: Path,
) -> None:
    # Given: classified local-start and configuration failures.
    temporary = ClassifiedQ1Executor(
        StageExecutionError(
            "content-assembler",
            "local log unavailable",
            StageFailureType.TEMPORARY_IO.value,
            True,
        )
    )
    configured = ClassifiedQ1Executor(
        StageExecutionError(
            "content-assembler",
            "unknown model",
            StageFailureType.INVALID_CONFIGURATION.value,
        )
    )

    # When: each failure reaches the Q1 runner boundary.
    temporary_result = run_job(_request(tmp_path / "temporary", temporary))
    configured_result = run_job(_request(tmp_path / "configured", configured))

    # Then: temporary I/O gets one runner retry; permanent config gets none.
    assert temporary_result.status is RunStatus.FAILED
    assert temporary.calls.count("content-assembler") == 2
    assert configured_result.status is RunStatus.FAILED
    assert configured.calls.count("content-assembler") == 1
