from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import ExternalWriteRequest
from tools.manifest import verify_manifest
from tools.runner_execution import recover_job, resume_job, run_job
from tools.runner_state import input_fingerprint
from tools.runner_types import (
    RunnerRequest,
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
    TopicSelectionContext,
)
from tools.topic_feedback_pinning import pin_daily_request

NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


class PinFixtureExecutor:
    def __init__(self, *, mutate_config: bool = False, fail_writer: bool = False) -> None:
        self.mutate_config: bool = mutate_config
        self.fail_writer: bool = fail_writer

    def execute(self, context: StageExecutionContext) -> StageResult:
        keyword = context.keyword or "topic"
        root = context.root
        if context.stage == "topic-selector":
            path = root / "research" / f"topic-selection-{keyword}.md"
            path.parent.mkdir(exist_ok=True)
            _ = path.write_text("# topic\n", encoding="utf-8")
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
        elif context.stage == "writer":
            path = root / "drafts" / f"{keyword}.md"
            path.parent.mkdir(exist_ok=True)
            _ = path.write_text("# draft\n", encoding="utf-8")
            if self.fail_writer:
                return StageResult(RunStatus.FAILED, StageExecution.ATTEMPTED, "pause")
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                artifacts=(path.relative_to(root).as_posix(),),
            )
        elif context.stage == "image-maker":
            asset_dir = root / "assets" / keyword
            asset_dir.mkdir(parents=True, exist_ok=True)
            _ = (asset_dir / "body.png").write_bytes(b"body")
            _ = (asset_dir / "thumbnail.png").write_bytes(b"thumbnail")
            _ = (asset_dir / "image-map.md").write_text("# images\n", encoding="utf-8")
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                artifacts=tuple(
                    f"assets/{keyword}/{name}"
                    for name in ("body.png", "thumbnail.png", "image-map.md")
                ),
            )
        elif context.stage == "content-assembler":
            final_dir = root / "final"
            final_dir.mkdir(exist_ok=True)
            _ = (final_dir / f"{keyword}.md").write_text(
                f"# {keyword}\n\n![body](../assets/{keyword}/body.png)\n",
                encoding="utf-8",
            )
            for suffix in ("-naver-layout.md", "-naver-copy.md", "-naver-input.md"):
                _ = (final_dir / f"{keyword}{suffix}").write_text(
                    f"# {keyword}\n", encoding="utf-8"
                )
            if self.mutate_config:
                _ = (root / "notion-config.md").write_text(
                    "- 데이터 소스 ID: `attacker-target`\n", encoding="utf-8"
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
        raise AssertionError(f"unexpected stage: {context.stage}")


class CapturingNotion:
    def __init__(self) -> None:
        self.calls: list[ExternalWriteRequest] = []

    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        self.calls.append(request)
        content_digest = "sha256:" + "1" * 64
        return {
            "storage_integrity": "passed",
            "notion_page_id": "page-pinned",
            "notion_last_verified_at": NOW.isoformat(),
            "expected_notion_content_digest": content_digest,
            "notion_content_digest": content_digest,
            "notion_roundtrip_digest": content_digest,
            "artifact_digest": verify_manifest(
                request.root, request.manifest_path
            ).artifact_digest,
        }


def _request(tmp_path: Path, executor: PinFixtureExecutor, notion: CapturingNotion) -> RunnerRequest:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `original-target`\n", encoding="utf-8"
    )
    return RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        auto_topic=True,
        now=NOW,
        selection_context=TopicSelectionContext("", "", "", "2026-09-01"),
        executor=executor,
        notion_adapter=notion,
    )


def test_live_run_pins_target_before_producers_and_records_identity(tmp_path: Path) -> None:
    # Given: a live Notion run with an approved source configured before stage one.
    notion = CapturingNotion()

    # When: the full content pipeline executes.
    request = _request(tmp_path, PinFixtureExecutor(), notion)
    result = run_job(request)
    pinned_request = pin_daily_request(request)

    # Then: state, input identity, and adapter request retain the same pinned target.
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert result.status is RunStatus.READY_FOR_NAVER
    assert state["notion_target_id"] == "original-target"
    assert state["input_hash"].startswith("sha256:")
    assert state["input_hash"] == input_fingerprint(
        replace(pinned_request, notion_target_id="original-target")
    )
    assert state["input_hash"] != input_fingerprint(
        replace(pinned_request, notion_target_id="different-target")
    )
    assert [call.target_id for call in notion.calls] == ["original-target"]
    events = [
        json.loads(line)
        for line in result.log_path.read_text(encoding="utf-8").splitlines()
    ]
    q1_event = next(event for event in events if event["stage"] == "content-assembler")
    q1_quality = q1_event["quality"]
    assert isinstance(q1_quality, dict)
    assert q1_quality["artifact_digest"] == state["artifact_digest"]
    notion_event = next(event for event in events if event["stage"] == "notion-rider")
    quality = notion_event["quality"]
    assert isinstance(quality, dict)
    assert quality["notion_target_id"] == "original-target"


def test_config_mutation_after_producer_fails_before_notion_adapter_call(tmp_path: Path) -> None:
    # Given: a producer that changes the config after the runner has pinned its target.
    notion = CapturingNotion()

    # When: the Notion stage is reached.
    result = run_job(_request(tmp_path, PinFixtureExecutor(mutate_config=True), notion))

    # Then: the original target remains recorded and no external adapter is called.
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert result.status is RunStatus.FAILED
    assert state["notion_target_id"] == "original-target"
    assert "target changed" in result.message
    assert notion.calls == []


def test_live_resume_rejects_missing_or_mismatched_pinned_target_before_adapter(
    tmp_path: Path,
) -> None:
    # Given: an interrupted live run with a durable original target.
    original_notion = CapturingNotion()
    interrupted = run_job(
        _request(tmp_path, PinFixtureExecutor(fail_writer=True), original_notion)
    )
    state = json.loads(interrupted.state_path.read_text(encoding="utf-8"))
    assert state["notion_target_id"] == "original-target"
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `attacker-target`\n", encoding="utf-8"
    )
    resumed_notion = CapturingNotion()

    # When / Then: recovery refuses the changed configuration before any adapter call.
    with pytest.raises(ContractError, match="pinned Notion target"):
        _ = resume_job(RunnerRequest(
            root=tmp_path,
            job="",
            run_id=interrupted.run_id,
            executor=PinFixtureExecutor(),
            notion_adapter=resumed_notion,
        ))
    assert resumed_notion.calls == []


def test_live_resume_uses_the_persisted_target_for_the_notion_request(
    tmp_path: Path,
) -> None:
    # Given: an interrupted live run with an unchanged configured target.
    interrupted = run_job(
        _request(tmp_path, PinFixtureExecutor(fail_writer=True), CapturingNotion())
    )
    resumed_notion = CapturingNotion()

    # When: the runner resumes the incomplete work.
    result = resume_job(RunnerRequest(
        root=tmp_path,
        job="",
        run_id=interrupted.run_id,
        executor=PinFixtureExecutor(),
        notion_adapter=resumed_notion,
    ))

    # Then: the persisted original target controls the first Notion request.
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert [call.target_id for call in resumed_notion.calls] == ["original-target"]
    assert result.status is RunStatus.READY_FOR_NAVER
    assert state["notion_target_id"] == "original-target"


def test_live_recover_blocks_changed_target_before_adapter(tmp_path: Path) -> None:
    # Given: an interrupted live run with a later config substitution.
    interrupted = run_job(
        _request(tmp_path, PinFixtureExecutor(fail_writer=True), CapturingNotion())
    )
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `attacker-target`\n", encoding="utf-8"
    )
    recovered_notion = CapturingNotion()

    # When: recovery checks its recorded input identity.
    result = recover_job(RunnerRequest(
        root=tmp_path,
        job="",
        run_id=interrupted.run_id,
        executor=PinFixtureExecutor(),
        notion_adapter=recovered_notion,
    ))

    # Then: recovery remains blocked before the adapter can write or authenticate.
    assert result.status is RunStatus.BLOCKED
    assert recovered_notion.calls == []


def test_live_resume_rejects_missing_pinned_target_before_adapter(tmp_path: Path) -> None:
    # Given: an interrupted live run whose state loses its pinned target.
    original_notion = CapturingNotion()
    interrupted = run_job(
        _request(tmp_path, PinFixtureExecutor(fail_writer=True), original_notion)
    )
    state = json.loads(interrupted.state_path.read_text(encoding="utf-8"))
    _ = state.pop("notion_target_id")
    _ = interrupted.state_path.write_text(json.dumps(state), encoding="utf-8")
    resumed_notion = CapturingNotion()

    # When / Then: state reconstruction refuses to enable the live adapter.
    with pytest.raises(ContractError, match="pinned Notion target"):
        _ = resume_job(RunnerRequest(
            root=tmp_path,
            job="",
            run_id=interrupted.run_id,
            executor=PinFixtureExecutor(),
            notion_adapter=resumed_notion,
        ))
    assert resumed_notion.calls == []
