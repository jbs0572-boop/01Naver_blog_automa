from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import ExternalWriteRequest
from tools.runner_cli import main as runner_main
from tools.runner_execution import confirm_job, job_key
from tools.runner_job import validate_job_request
from tools.runner_types import (
    STAGE_ORDER,
    ConfirmationInput,
    RunnerRequest,
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
)

NOW = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)


class FixtureExecutor:
    def __init__(self, replacement_keyword: str | None = None) -> None:
        self.replacement_keyword: str | None = replacement_keyword

    def execute(self, context: StageExecutionContext) -> StageResult:
        root = context.root
        stage = context.stage
        keyword = context.keyword or "fixture"
        (root / "research").mkdir(exist_ok=True)
        (root / "drafts").mkdir(exist_ok=True)
        asset_dir = root / "assets" / keyword
        final_dir = root / "final"
        asset_dir.mkdir(parents=True, exist_ok=True)
        final_dir.mkdir(exist_ok=True)
        artifacts: tuple[str, ...]
        if stage == "topic-selector":
            path = root / "research" / f"topic-selection-{keyword}.md"
            _ = path.write_text("# selection\n", encoding="utf-8")
            artifacts = (path.relative_to(root).as_posix(),)
            return StageResult(
                RunStatus.PASSED,
                StageExecution.PRODUCED,
                resolved_keyword=self.replacement_keyword or keyword,
                artifacts=artifacts,
            )
        if stage == "researcher":
            path = root / "research" / f"{keyword}.md"
            _ = path.write_text("# research\n", encoding="utf-8")
            artifacts = (path.relative_to(root).as_posix(),)
        elif stage == "writer":
            path = root / "drafts" / f"{keyword}.md"
            _ = path.write_text("# draft\n", encoding="utf-8")
            artifacts = (path.relative_to(root).as_posix(),)
        elif stage == "image-maker":
            for name, data in (("body.png", b"body"), ("thumbnail.png", b"thumb")):
                _ = (asset_dir / name).write_bytes(data)
            _ = (asset_dir / "image-map.md").write_text("# map\n", encoding="utf-8")
            artifacts = tuple(
                f"assets/{keyword}/{name}"
                for name in ("body.png", "thumbnail.png", "image-map.md")
            )
        else:
            _ = (final_dir / f"{keyword}.md").write_text(
                f"# {keyword}\n\n![body](../assets/{keyword}/body.png)\n",
                encoding="utf-8",
            )
            for suffix in ("-naver-layout.md", "-naver-copy.md", "-naver-input.md"):
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
        return StageResult(
            RunStatus.PASSED, StageExecution.PRODUCED, artifacts=artifacts
        )


class FixtureNotion:
    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        from tools.manifest import verify_manifest

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


def test_daily_generate_accepts_auto_topic(tmp_path: Path) -> None:
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword=None,
        auto_topic=True,
        now=NOW,
    )

    assert validate_job_request(request) == "daily-generate"
    assert job_key(request).startswith("sha256:")


@pytest.mark.parametrize(
    "candidate",
    [
        RunnerRequest(root=Path("."), job="daily-generate", keyword=None),
        RunnerRequest(
            root=Path("."),
            job="daily-generate",
            keyword="topic",
            auto_topic=True,
        ),
    ],
)
def test_daily_generate_rejects_wrong_topic_input(candidate: RunnerRequest) -> None:
    with pytest.raises(ContractError):
        _job = validate_job_request(candidate)


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--keyword", "topic", "--auto-topic"],
        ["--mode", "formal", "--auto-topic"],
        ["--auto-topic", "--foo", "bar"],
        ["--keyword", "first", "--keyword", "second"],
    ],
)
def test_daily_generate_cli_rejects_invalid_topic_contract_before_state(
    tmp_path: Path, options: list[str]
) -> None:
    exit_code = runner_main(
        ["automation-runner", "run", "daily-generate", "--root", str(tmp_path), *options]
    )

    assert exit_code == 2
    assert not (tmp_path / ".automation").exists()


@pytest.mark.parametrize(
    ("keyword", "auto_topic", "topic_source"),
    [
        ("user-topic", False, "user_defined"),
        (None, True, "auto_selected"),
    ],
)
def test_both_topic_sources_follow_same_pipeline_to_draft_saved(
    tmp_path: Path,
    keyword: str | None,
    auto_topic: bool,
    topic_source: str,
) -> None:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword=keyword,
        auto_topic=auto_topic,
        now=NOW,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=FixtureNaver(),
    )

    from tools.runner_execution import run_job

    waiting = run_job(request)
    assert waiting.status is RunStatus.AWAITING_USER_CONFIRMATION
    waiting_state = json.loads(waiting.state_path.read_text(encoding="utf-8"))
    assert waiting_state["topic_source"] == topic_source
    assert set(waiting_state["stages"]) == set(STAGE_ORDER)
    assert all(
        waiting_state["stages"][stage] == "passed" for stage in STAGE_ORDER
    )
    events = [
        json.loads(line)
        for line in waiting.log_path.read_text(encoding="utf-8").splitlines()
    ]
    stage_events = [item for item in events if item["event_type"] == "stage"]
    assert [item["stage"] for item in stage_events] == list(STAGE_ORDER)
    assert all(item["topic_source"] == topic_source for item in stage_events)
    saved = confirm_job(ConfirmationInput(
        tmp_path,
        waiting.run_id,
        "naver-draft-save",
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=FixtureNaver(),
    ))
    assert saved.status is RunStatus.DRAFT_SAVED


def test_user_defined_topic_cannot_be_replaced_by_selector(tmp_path: Path) -> None:
    from tools.runner_execution import run_job

    result = run_job(RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="user-topic",
        now=NOW,
        executor=FixtureExecutor("replacement-topic"),
    ))

    assert result.status is RunStatus.FAILED
    assert "cannot replace a user-defined keyword" in result.message


def test_pipeline_completes_content_when_external_adapters_are_absent(
    tmp_path: Path,
) -> None:
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        auto_topic=True,
        now=NOW,
        executor=FixtureExecutor(),
    )

    from tools.runner_execution import run_job

    result = run_job(request)

    assert result.status is RunStatus.LOCAL_ONLY
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    stages = state["stages"]
    assert stages["content-assembler"] == "passed"
    assert stages["notion-rider"] == "skipped"
    assert stages["naver-rider"] == "skipped"
    assert "external storage" in result.message
