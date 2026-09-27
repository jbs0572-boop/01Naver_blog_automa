from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import ExternalWriteRequest
from tools.notion_resume import NotionQ2Failure
from tools.runner_cli import main as runner_main
from tools.runner_execution import confirm_job, job_key, recover_job, resume_job
from tools.runner_job import validate_job_request
from tools.runner_types import (
    STAGE_ORDER,
    ConfirmationInput,
    RunnerRequest,
    RunnerResult,
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


class FailingQ2Notion:
    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        _ = request
        raise NotionQ2Failure("Notion round-trip content digest does not match")


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


class CountingNaver(FixtureNaver):
    def __init__(self) -> None:
        self.prepare_calls: int = 0
        self.save_calls: int = 0

    @property
    @override
    def target_blog_id(self) -> str:
        return "blog-fixture"

    @override
    def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
        self.prepare_calls += 1
        return super().prepare(title, body, artifact_digest)

    @override
    def save(self, title: str, artifact_digest: str) -> JSONMap:
        self.save_calls += 1
        return super().save(title, artifact_digest)


class MutableTargetNaver(CountingNaver):
    def __init__(self) -> None:
        super().__init__()
        self.blog_id: str = "blog-fixture"

    @property
    @override
    def target_blog_id(self) -> str:
        return self.blog_id


class StaleDigestNotion(FixtureNotion):
    @override
    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        result = super().write_and_verify(request)
        result["notion_roundtrip_digest"] = "sha256:" + "0" * 64
        return result


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
    ("job_arguments", "factory_calls"),
    (
        (("run", "daily-generate", "--auto-topic"), 1),
        (("run", "daily-generate", "--auto-topic", "--dry-run"), 0),
        (("run", "weekly-improve"), 0),
    ),
)
def test_cli_injects_deferred_notion_adapter_only_for_live_content_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    job_arguments: tuple[str, ...],
    factory_calls: int,
) -> None:
    for name in (
        "topic-selector.md",
        "researcher.md",
        "writer.md",
        "image-maker.md",
        "content-assembler.md",
        "notion-rider.md",
        "naver-rider.md",
        "notion-config.md",
    ):
        _ = (tmp_path / name).write_text("configured\n", encoding="utf-8")
    notion = FixtureNotion()
    created: list[Path] = []
    captured: list[RunnerRequest] = []

    def factory(root: Path) -> FixtureNotion:
        created.append(root)
        return notion

    def capture(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            "RUN-cli-adapter",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state.json",
            tmp_path / "run.jsonl",
            (),
            "captured",
        )

    monkeypatch.setattr("tools.runner_cli.run_job", capture)
    exit_code = runner_main(
        ["automation-runner", *job_arguments, "--root", str(tmp_path)],
        notion_adapter_factory=factory,
    )

    assert exit_code == 0
    assert len(created) == factory_calls
    assert len(captured) == 1
    assert captured[0].notion_adapter is (notion if factory_calls else None)


@pytest.mark.parametrize(
    ("job_arguments", "factory_calls"),
    (
        (("run", "daily-generate", "--auto-topic"), 1),
        (("run", "daily-generate", "--auto-topic", "--dry-run"), 0),
        (("run", "weekly-improve"), 0),
    ),
)
def test_cli_uses_default_notion_factory_only_for_live_content_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    job_arguments: tuple[str, ...],
    factory_calls: int,
) -> None:
    # Given: the default factory is observable without importing the API adapter.
    notion = FixtureNotion()
    created: list[Path] = []
    captured: list[RunnerRequest] = []

    def default_factory(root: Path) -> FixtureNotion:
        created.append(root)
        return notion

    def capture(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            "RUN-cli-default-adapter",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state.json",
            tmp_path / "run.jsonl",
            (),
            "captured",
        )

    monkeypatch.setattr("tools.runner_cli._default_notion_adapter_factory", default_factory)
    monkeypatch.setattr("tools.runner_cli.run_job", capture)

    # When: the public CLI runs without an injected test factory.
    exit_code = runner_main(
        ["automation-runner", *job_arguments, "--root", str(tmp_path)]
    )

    # Then: only a live daily content run constructs the deferred adapter.
    assert exit_code == 0
    assert len(created) == factory_calls
    assert len(captured) == 1
    assert captured[0].notion_adapter is (notion if factory_calls else None)


@pytest.mark.parametrize(
    ("command", "extra", "expected_calls"),
    (
        ("resume", (), 1),
        ("recover", (), 1),
        ("recover", ("--dry-run",), 0),
    ),
)
def test_cli_reinjects_deferred_notion_adapter_for_resumable_live_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    extra: tuple[str, ...],
    expected_calls: int,
) -> None:
    notion = FixtureNotion()
    created: list[Path] = []
    captured: list[RunnerRequest] = []

    def factory(root: Path) -> FixtureNotion:
        created.append(root)
        return notion

    def capture(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            "RUN-cli-resume",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state.json",
            tmp_path / "run.jsonl",
            (),
            "captured",
        )

    monkeypatch.setattr(f"tools.runner_cli.{command}_job", capture)
    exit_code = runner_main(
        [
            "automation-runner",
            command,
            "--run-id",
            "RUN-cli-resume",
            *extra,
            "--root",
            str(tmp_path),
        ],
        notion_adapter_factory=factory,
    )

    assert exit_code == 0
    assert len(created) == expected_calls
    assert len(captured) == 1
    assert captured[0].notion_adapter is (notion if expected_calls else None)


def test_recover_preserves_injected_notion_adapter_after_state_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notion = FixtureNotion()
    reconstructed = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="topic",
        run_id="RUN-recover-adapter",
        resume=True,
    )
    captured: list[RunnerRequest] = []

    def reconstructed_state(_path: Path) -> JSONMap:
        return {"input_hash": "same"}

    def reconstructed_request(
        _state: JSONMap,
        _root: Path,
        _state_dir: Path | None,
    ) -> RunnerRequest:
        return reconstructed

    def unchanged_fingerprint(_request: RunnerRequest) -> str:
        return "same"

    monkeypatch.setattr(
        "tools.runner_execution.read_state",
        reconstructed_state,
    )
    monkeypatch.setattr(
        "tools.runner_execution.request_from_state",
        reconstructed_request,
    )
    monkeypatch.setattr(
        "tools.runner_execution.input_fingerprint",
        unchanged_fingerprint,
    )

    def capture(request: RunnerRequest, allow_existing: bool = False) -> RunnerResult:
        assert allow_existing is True
        captured.append(request)
        return RunnerResult(
            "RUN-recover-adapter",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state.json",
            tmp_path / "run.jsonl",
            (),
            "captured",
        )

    monkeypatch.setattr("tools.runner_execution._run", capture)

    _ = recover_job(RunnerRequest(
        root=tmp_path,
        job="",
        run_id="RUN-recover-adapter",
        notion_adapter=notion,
    ))

    assert len(captured) == 1
    assert captured[0].notion_adapter is notion


def test_resume_drops_external_adapters_for_a_recovered_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an interrupted dry-run and externally supplied adapters.
    recovered = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="topic",
        run_id="RUN-resume-dry-run",
        dry_run=True,
        resume=True,
    )
    captured: list[RunnerRequest] = []

    def persisted_state(_path: Path) -> JSONMap:
        return {}

    def persisted_request(
        _state: JSONMap, _root: Path, _state_dir: Path | None
    ) -> RunnerRequest:
        return recovered

    def capture(request: RunnerRequest, allow_existing: bool) -> RunnerResult:
        assert allow_existing is True
        captured.append(request)
        return RunnerResult(
            "RUN-resume-dry-run",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state.json",
            tmp_path / "run.jsonl",
            (),
            "captured",
        )

    monkeypatch.setattr("tools.runner_execution.read_state", persisted_state)
    monkeypatch.setattr("tools.runner_execution.request_from_state", persisted_request)
    monkeypatch.setattr("tools.runner_execution._run", capture)

    # When: resume reconstructs the persisted request.
    _ = resume_job(RunnerRequest(
        root=tmp_path,
        job="",
        run_id="RUN-resume-dry-run",
        notion_adapter=FixtureNotion(),
        naver_adapter=FixtureNaver(),
    ))

    # Then: the recovered dry-run cannot retain either external adapter.
    assert len(captured) == 1
    assert captured[0].dry_run is True
    assert captured[0].notion_adapter is None
    assert captured[0].naver_adapter is None


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
    notion_quality = next(
        item["quality"]
        for item in stage_events
        if item["stage"] == "notion-rider"
    )
    assert notion_quality["storage_integrity"] == "passed"
    assert notion_quality["external_call"] is True
    assert notion_quality["notion_page_id"] == "page-fixture"
    assert notion_quality["notion_last_verified_at"] == NOW.isoformat()
    assert notion_quality["notion_roundtrip_digest"].startswith("sha256:")
    saved = confirm_job(ConfirmationInput(
        tmp_path,
        waiting.run_id,
        "naver-draft-save",
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=FixtureNaver(),
    ))
    assert saved.status is RunStatus.DRAFT_SAVED


def test_q2_failure_is_recorded_and_blocks_naver(tmp_path: Path) -> None:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        executor=FixtureExecutor(),
        notion_adapter=FailingQ2Notion(),
        naver_adapter=FixtureNaver(),
    )

    from tools.runner_execution import run_job

    result = run_job(request)

    assert result.status is RunStatus.FAILED
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert state["storage_integrity"] == "failed"
    assert state["stages"]["notion-rider"] == "failed"
    assert state["stages"]["naver-rider"] == "skipped"
    events = [
        json.loads(line)
        for line in result.log_path.read_text(encoding="utf-8").splitlines()
    ]
    notion_event = next(
        item for item in events if item.get("stage") == "notion-rider"
    )
    assert notion_event["quality"]["storage_integrity"] == "failed"
    assert notion_event["quality"]["external_call"] is True


def test_naver_gate_failure_prevents_prepare(tmp_path: Path) -> None:
    # Given: Q2 reports an artifact digest that is stale for the current manifest.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()

    # When: the pipeline reaches the Naver preparation boundary.
    from tools.runner_execution import run_job

    result = run_job(RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        executor=FixtureExecutor(),
        notion_adapter=StaleDigestNotion(),
        naver_adapter=naver,
    ))

    # Then: fresh authorization fails before either browser mutation is invoked.
    assert result.status is RunStatus.FAILED
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0


def test_stale_confirmation_resume_revalidates_gate_before_save(
    tmp_path: Path,
) -> None:
    # Given: a valid preview whose persisted Q2 log is made stale before confirmation.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()
    from tools.runner_execution import run_job

    waiting = run_job(RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=naver,
    ))
    events = [
        json.loads(line)
        for line in waiting.log_path.read_text(encoding="utf-8").splitlines()
    ]
    notion_event = next(
        event for event in events if event.get("stage") == "notion-rider"
    )
    notion_event["quality"]["notion_roundtrip_digest"] = "sha256:" + "0" * 64
    _ = waiting.log_path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )

    # When: the operator confirms the already-prepared draft.
    result = confirm_job(ConfirmationInput(
        tmp_path,
        waiting.run_id,
        "naver-draft-save",
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=naver,
    ))

    # Then: save is denied by a fresh gate check and preparation is not repeated.
    assert result.status is RunStatus.FAILED
    assert naver.prepare_calls == 1
    assert naver.save_calls == 0


def test_confirmation_resume_rejects_changed_blog_before_save(
    tmp_path: Path,
) -> None:
    # Given: a valid preview whose adapter target changes before confirmation.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = MutableTargetNaver()
    from tools.runner_execution import run_job

    waiting = run_job(RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=naver,
    ))
    naver.blog_id = "blog-other"

    # When: the operator confirms the stale target preview.
    result = confirm_job(ConfirmationInput(
        tmp_path,
        waiting.run_id,
        "naver-draft-save",
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=naver,
    ))

    # Then: the current target binding fails before any save interaction.
    assert result.status is RunStatus.FAILED
    assert naver.prepare_calls == 1
    assert naver.save_calls == 0


def test_ready_for_naver_resume_revalidates_persisted_q2_before_prepare(
    tmp_path: Path,
) -> None:
    # Given: a Notion-only run whose persisted Q2 digest becomes stale.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    from tools.runner_execution import run_job

    ready = run_job(RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
    ))
    state = json.loads(ready.state_path.read_text(encoding="utf-8"))
    state["notion_roundtrip_digest"] = "sha256:" + "0" * 64
    _ = ready.state_path.write_text(json.dumps(state), encoding="utf-8")
    naver = CountingNaver()

    # When: the ready run resumes with a Naver adapter.
    result = resume_job(RunnerRequest(
        root=tmp_path,
        job="",
        run_id=ready.run_id,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=naver,
    ))

    # Then: persisted Q2 is checked before any browser preparation or save.
    assert result.status is RunStatus.FAILED
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0


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


def test_notion_only_pipeline_reaches_ready_for_naver_without_confirmation(
    tmp_path: Path,
) -> None:
    # Given: a complete local pipeline with a Notion adapter but no Naver adapter.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
    )

    from tools.runner_execution import run_job

    # When: the actual pipeline executes through Notion Q2.
    result = run_job(request)
    state = json.loads(result.state_path.read_text(encoding="utf-8"))

    # Then: Naver is skipped and no save confirmation is created.
    assert result.status is RunStatus.READY_FOR_NAVER
    assert state["stages"]["notion-rider"] == "passed"
    assert state["stages"]["naver-rider"] == "skipped"
    assert state["confirmation"] is None


def test_active_contract_describes_dashboard_three_child_fanout() -> None:
    contract = Path(__file__).parents[1] / "EXECUTION_AGENT.md"
    text = contract.read_text(encoding="utf-8")
    required_markers = (
        "dashboard_auto_envelope=three_sequential_daily_generate_children",
        "dashboard_user_envelope=one_daily_generate_child",
        "child_lifecycle=q1_then_q2_then_confirmation",
    )
    assert all(marker in text for marker in required_markers)


def test_topic_selector_contract_describes_shared_snapshot_reuse() -> None:
    contract = Path(__file__).parents[1] / "topic-selector.md"
    text = contract.read_text(encoding="utf-8")
    required_markers = (
        "snapshot_reuse=explicit_slot_context",
        "capture_once -> reuse_only",
        "ordinary daily-generate",
    )
    assert all(marker in text for marker in required_markers)


def test_design_contract_forbids_bulk_confirmation() -> None:
    contract = Path(__file__).parents[1] / "DESIGN.md"
    text = contract.read_text(encoding="utf-8")
    required_markers = (
        "bulk_confirmation=forbidden",
        "child_confirmation=independent",
    )
    assert all(marker in text for marker in required_markers)


@pytest.mark.parametrize(
    "status",
    (
        RunStatus.READY_FOR_NAVER,
        RunStatus.AWAITING_USER_CONFIRMATION,
        RunStatus.DRAFT_SAVED,
    ),
)
def test_cli_treats_successful_external_pause_states_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: RunStatus,
) -> None:
    def completed(_request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            "RUN-cli-external-state",
            status,
            tmp_path / "state.json",
            tmp_path / "run.jsonl",
            (),
            "completed",
        )

    monkeypatch.setattr("tools.runner_cli.run_job", completed)

    exit_code = runner_main([
        "automation-runner",
        "run",
        "daily-generate",
        "--root",
        str(tmp_path),
        "--auto-topic",
        "--dry-run",
    ])

    assert exit_code == 0
