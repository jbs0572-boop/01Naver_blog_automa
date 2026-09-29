from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override

import pytest

from tests.article_quality_fixtures import install_passing_quality_review
from tools.article_quality import assessment_digest, assessment_path
from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.external_adapter import ExternalWriteRequest
from tools.image_quality import post_q2_image_review_path
from tools.manifest import verify_manifest
from tools.model_presets import default_stage_settings, model_config_snapshot
from tools.notion_resume import NotionQ2Failure
from tools.runner_cli import _live_naver_adapter
from tools.runner_cli import main as runner_main
from tools.runner_execution import (
    confirm_job,
    invalidate_naver_preparation,
    job_key,
    recover_job,
    resume_job,
    run_job,
)
from tools.runner_job import find_duplicate_job, validate_job_request
from tools.runner_state import atomic_write_json, state_paths
from tools.runner_types import (
    STAGE_ORDER,
    ConfirmationInput,
    RunnerRequest,
    RunnerResult,
    RunStatus,
    StageExecution,
    StageExecutionContext,
    StageResult,
    TopicSelectionContext,
)

NOW = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
DATE_CONTEXT = TopicSelectionContext("", "", "", "2026-09-07")


@pytest.fixture(autouse=True)
def rollout_contract(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    _ = shutil.copy(
        Path(__file__).resolve().parents[1] / "config/topic-feedback-rollout.json",
        config,
    )


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
                    f"[TITLE]{keyword} title[/TITLE]\n"
                    + "[IMAGE file=\"thumbnail.png\" alt=\"대표\" representative=true]\n"
                    + "[ALT]대표 이미지[/ALT]\n"
                    + "[IMAGE file=\"body.png\" alt=\"본문\" representative=false]\n"
                    + "[ALT]본문 이미지[/ALT]\n[TEXT]Fixture body[/TEXT]\n",
                    encoding="utf-8",
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


class MismatchedNaverInputExecutor(FixtureExecutor):
    @override
    def execute(self, context: StageExecutionContext) -> StageResult:
        result = super().execute(context)
        if context.stage == "content-assembler" and context.keyword is not None:
            path = context.root / "final" / f"{context.keyword}-naver-input.md"
            source = path.read_text(encoding="utf-8")
            _ = path.write_text(
                source.replace("Fixture body", "Different unreviewed body"),
                encoding="utf-8",
            )
        return result


class FixtureNotion:
    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        from tools.manifest import verify_manifest

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


class LowQualityNotion(FixtureNotion):
    @override
    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        result = super().write_and_verify(request)
        manifest = verify_manifest(request.root, request.manifest_path)
        path = assessment_path(request.root, request.run_id)
        raw_report: JSONValue = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(raw_report, dict)
        report: JSONMap = raw_report
        raw_scores = report.get("scores")
        assert isinstance(raw_scores, dict)
        scores: JSONMap = raw_scores
        scores["factual_accuracy"] = 0
        scores["source_completeness"] = 8
        report["cause_type"] = "topic_unsuitable"
        report["failure_stage"] = "researcher"
        report["next_action"] = "reject this topic and choose a supported one"
        report["report_digest"] = assessment_digest(report)
        _ = path.write_text(json.dumps(report), encoding="utf-8")
        assert manifest.artifact_digest == report["artifact_digest"]
        return result


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


def _run_through_q3_fixture(request: RunnerRequest) -> RunnerResult:
    result = run_job(request)
    if result.status is RunStatus.READY_FOR_NAVER:
        result = resume_job(replace(request, run_id=result.run_id, resume=True))
    return result


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


class InterruptFirstSaveNaver(CountingNaver):
    def __init__(self) -> None:
        super().__init__()
        self.save_attempts: int = 0

    @override
    def save(self, title: str, artifact_digest: str) -> JSONMap:
        self.save_attempts += 1
        if self.save_attempts == 1:
            raise ContractError("simulated interruption before Naver save")
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
        selection_context=TopicSelectionContext("", "", "", "2026-09-07"),
    )

    assert validate_job_request(request) == "daily-generate"
    assert job_key(request).startswith("sha256:")


def test_new_daily_generate_rejects_missing_human_as_of_date() -> None:
    # Given: a new request selects exactly one valid topic source but has no date.
    request = RunnerRequest(
        root=Path("."),
        job="daily-generate",
        auto_topic=True,
    )

    # When/Then: validation fails closed before any run state can be created.
    with pytest.raises(ContractError, match="daily-generate requires --as-of-date"):
        _ = validate_job_request(request)


def test_daily_generate_cli_rejects_missing_date_before_adapter_creation(
    tmp_path: Path,
) -> None:
    # Given: a date-less live daily request and an observable adapter factory.
    factory_calls = 0

    def factory(_root: Path) -> FixtureNotion:
        nonlocal factory_calls
        factory_calls += 1
        return FixtureNotion()

    # When: the command enters through the real runner CLI boundary.
    exit_code = runner_main(
        [
            "automation-runner",
            "run",
            "daily-generate",
            "--auto-topic",
            "--root",
            str(tmp_path),
        ],
        notion_adapter_factory=factory,
    )

    # Then: the request fails before credentials/adapters or state are touched.
    assert exit_code == 2
    assert factory_calls == 0
    assert not (tmp_path / ".automation").exists()


def test_same_date_auto_batch_slots_have_distinct_job_keys(tmp_path: Path) -> None:
    # Given
    slot_one = TopicSelectionContext(
        "",
        "",
        "",
        "2026-09-07",
        batch_id="BATCH-same-day",
        batch_slot=1,
        snapshot_policy="capture_once",
        capture_id="CAPTURE-same-day",
        snapshot_path="metadata/creator-advisor/2026-09-07/CAPTURE-same-day.json",
    )
    slot_two = TopicSelectionContext(
        "",
        "",
        "",
        "2026-09-07",
        batch_id="BATCH-same-day",
        batch_slot=2,
        snapshot_policy="reuse_only",
        capture_id="CAPTURE-same-day",
        snapshot_path="metadata/creator-advisor/2026-09-07/CAPTURE-same-day.json",
        snapshot_sha256="sha256:" + "b" * 64,
        excluded_keywords=("첫 주제",),
    )
    first = RunnerRequest(
        tmp_path,
        "daily-generate",
        auto_topic=True,
        selection_context=slot_one,
    )
    second = RunnerRequest(
        tmp_path,
        "daily-generate",
        auto_topic=True,
        selection_context=slot_two,
    )

    # When / Then
    assert job_key(first) != job_key(second)


def test_daily_generate_local_only_run_blocks_duplicate_until_external_continuation(
    tmp_path: Path,
) -> None:
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="topic",
        now=NOW,
        selection_context=DATE_CONTEXT,
    )
    run_id = "RUN-local-only-pending"
    state_path, _, _ = state_paths(tmp_path, run_id)
    atomic_write_json(
        state_path,
        {
            "run_id": run_id,
            "job": "daily-generate",
            "job_key": job_key(request),
            "status": RunStatus.LOCAL_ONLY.value,
        },
    )

    duplicate = find_duplicate_job(request, "daily-generate", None)

    assert duplicate is not None
    assert duplicate[0] == run_id


def test_daily_generate_model_snapshot_changes_duplicate_identity(tmp_path: Path) -> None:
    # Given: otherwise identical new requests pin distinct preset revisions.
    first = RunnerRequest(
        tmp_path,
        "daily-generate",
        keyword="topic",
        selection_context=DATE_CONTEXT,
        model_config=model_config_snapshot("default", 1, default_stage_settings()),
    )
    second = RunnerRequest(
        tmp_path,
        "daily-generate",
        keyword="topic",
        selection_context=DATE_CONTEXT,
        model_config=model_config_snapshot("default", 2, default_stage_settings()),
    )

    # When / Then: duplicate admission cannot collapse the newer snapshot.
    assert job_key(first) != job_key(second)


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
        [
            "automation-runner",
            "run",
            "daily-generate",
            "--root",
            str(tmp_path),
            *options,
        ]
    )

    assert exit_code == 2
    assert not (tmp_path / ".automation").exists()


@pytest.mark.parametrize(
    ("job_arguments", "factory_calls"),
    (
        (("run", "daily-generate", "--auto-topic", "--as-of-date", "2026-09-07"), 1),
        (
            (
                "run",
                "daily-generate",
                "--auto-topic",
                "--as-of-date",
                "2026-09-07",
                "--dry-run",
            ),
            0,
        ),
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
        (("run", "daily-generate", "--auto-topic", "--as-of-date", "2026-09-07"), 1),
        (
            (
                "run",
                "daily-generate",
                "--auto-topic",
                "--as-of-date",
                "2026-09-07",
                "--dry-run",
            ),
            0,
        ),
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

    monkeypatch.setattr(
        "tools.runner_cli._default_notion_adapter_factory", default_factory
    )
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
    if command == "resume":
        state_path, _, _ = state_paths(tmp_path, "RUN-cli-resume")
        atomic_write_json(
            state_path,
            {
                "run_id": "RUN-cli-resume",
                "job": "daily-generate",
                "status": RunStatus.RUNNING.value,
                "stages": {"notion-rider": "pending", "naver-rider": "pending"},
            },
        )
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


def test_cli_forwards_naver_confirmation_nonce_and_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ConfirmationInput] = []
    adapter = FixtureNaver()
    notion = FixtureNotion()

    def confirm(confirmation: ConfirmationInput) -> RunnerResult:
        captured.append(confirmation)
        return RunnerResult(
            "RUN-cli-confirm",
            RunStatus.DRAFT_SAVED,
            tmp_path / "state.json",
            tmp_path / "run.jsonl",
            (),
            "saved",
        )

    monkeypatch.setattr("tools.runner_cli.confirm_job", confirm)
    result = runner_main(
        [
            "automation-runner",
            "confirm",
            "--root",
            str(tmp_path),
            "--run-id",
            "RUN-cli-confirm",
            "--action",
            "naver-draft-save",
            "--confirmation-nonce",
            "nonce-from-current-preview",
        ],
        naver_adapter_factory=lambda _root: adapter,
        notion_adapter_factory=lambda _root: notion,
    )

    assert result == 0
    assert captured[0].confirmation_nonce == "nonce-from-current-preview"
    assert captured[0].naver_adapter is adapter
    assert captured[0].notion_adapter is notion


def test_cli_naver_adapter_preserves_recoverable_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = FixtureNaver()
    recovery_options: list[bool] = []
    closed: list[bool] = []

    class Gateway:
        def create_naver_adapter(self, *, discard_recovery: bool) -> FixtureNaver:
            recovery_options.append(discard_recovery)
            return adapter

        def close(self) -> None:
            closed.append(True)

    gateway = Gateway()
    monkeypatch.setattr(
        "tools.runner_cli.load_aside_browser_gateway",
        lambda *_args, **_kwargs: gateway,
    )

    actual, cleanup = _live_naver_adapter(tmp_path, None)
    cleanup()

    assert actual is adapter
    assert recovery_options == [False]
    assert closed == [True]


def test_cli_resume_reinjects_naver_adapter_after_q3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path, _, _ = state_paths(tmp_path, "RUN-cli-q3")
    atomic_write_json(
        state_path,
        {
            "run_id": "RUN-cli-q3",
            "job": "daily-generate",
            "status": RunStatus.READY_FOR_NAVER.value,
            "stages": {"notion-rider": "passed", "naver-rider": "pending"},
        },
    )
    adapter = FixtureNaver()
    captured: list[RunnerRequest] = []

    def capture(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            "RUN-cli-q3",
            RunStatus.AWAITING_USER_CONFIRMATION,
            state_path,
            tmp_path / "run.jsonl",
            (),
            "confirmation required",
        )

    monkeypatch.setattr("tools.runner_cli.resume_job", capture)
    result = runner_main(
        [
            "automation-runner",
            "resume",
            "--root",
            str(tmp_path),
            "--run-id",
            "RUN-cli-q3",
        ],
        notion_adapter_factory=lambda _root: FixtureNotion(),
        naver_adapter_factory=lambda _root: adapter,
    )

    assert result == 0
    assert captured[0].naver_adapter is adapter


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

    def unchanged_fingerprint(
        _request: RunnerRequest, **_kwargs: object
    ) -> str:
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

    def capture(
        request: RunnerRequest,
        allow_existing: bool = False,
        *,
        lease_held: bool = False,
    ) -> RunnerResult:
        assert allow_existing is True and lease_held is True
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

    _ = recover_job(
        RunnerRequest(
            root=tmp_path,
            job="",
            run_id="RUN-recover-adapter",
            notion_adapter=notion,
        )
    )

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

    def capture(
        request: RunnerRequest,
        allow_existing: bool,
        *,
        lease_held: bool,
    ) -> RunnerResult:
        assert allow_existing is True
        assert lease_held is False
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
    _ = resume_job(
        RunnerRequest(
            root=tmp_path,
            job="",
            run_id="RUN-resume-dry-run",
            notion_adapter=FixtureNotion(),
            naver_adapter=FixtureNaver(),
        )
    )

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
        selection_context=DATE_CONTEXT,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=FixtureNaver(),
    )


    waiting = _run_through_q3_fixture(request)
    assert waiting.status is RunStatus.AWAITING_USER_CONFIRMATION
    waiting_state = json.loads(waiting.state_path.read_text(encoding="utf-8"))
    assert waiting_state["topic_source"] == topic_source
    assert set(waiting_state["stages"]) == set(STAGE_ORDER)
    assert all(waiting_state["stages"][stage] == "passed" for stage in STAGE_ORDER)
    events = [
        json.loads(line)
        for line in waiting.log_path.read_text(encoding="utf-8").splitlines()
    ]
    stage_events = [item for item in events if item["event_type"] == "stage"]
    assert [item["stage"] for item in stage_events] == list(STAGE_ORDER)
    assert all(item["topic_source"] == topic_source for item in stage_events)
    notion_quality = next(
        item["quality"] for item in stage_events if item["stage"] == "notion-rider"
    )
    assert notion_quality["storage_integrity"] == "passed"
    assert notion_quality["external_call"] is True
    assert notion_quality["notion_page_id"] == "page-fixture"
    assert notion_quality["notion_last_verified_at"] == NOW.isoformat()
    assert notion_quality["notion_roundtrip_digest"].startswith("sha256:")
    saved = confirm_job(
        ConfirmationInput(
            tmp_path,
            waiting.run_id,
            "naver-draft-save",
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=FixtureNaver(),
            confirmation_nonce=str(waiting_state["confirmation_nonce"]),
        )
    )
    assert saved.status is RunStatus.DRAFT_SAVED


def test_auto_save_flag_cannot_bypass_explicit_naver_confirmation(
    tmp_path: Path,
) -> None:
    # Given: an otherwise valid live request carrying the legacy auto-save flag.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()

    # When: Q2 completes and the request reaches the Naver boundary unconfirmed.
    result = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
            auto_save_naver=True,
        )
    )

    # Then: preparation may occur, but saving remains blocked pending a real confirm.
    assert result.status is RunStatus.AWAITING_USER_CONFIRMATION
    assert result.message == "awaiting_user_confirmation"
    assert naver.prepare_calls == 1
    assert naver.save_calls == 0
    events = [
        json.loads(line)
        for line in result.log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(event.get("event_type") == "confirmation" for event in events)


def test_direct_confirmation_without_active_nonce_cannot_save_naver_draft(
    tmp_path: Path,
) -> None:
    # Given: a prepared Naver draft with a runner-owned active nonce.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()

    waiting = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
        )
    )

    # When: a direct legacy confirmation omits the active dashboard nonce.
    with pytest.raises(ContractError, match="confirmation nonce"):
        _ = confirm_job(
            ConfirmationInput(
                tmp_path,
                waiting.run_id,
                "naver-draft-save",
                executor=FixtureExecutor(),
                notion_adapter=FixtureNotion(),
                naver_adapter=naver,
            )
        )

    # Then: the state remains awaiting confirmation and Naver has not saved.
    state = json.loads(waiting.state_path.read_text(encoding="utf-8"))
    assert state["status"] == RunStatus.AWAITING_USER_CONFIRMATION.value
    assert naver.save_calls == 0


def test_explicit_confirmation_saves_after_blocked_auto_save_flag(
    tmp_path: Path,
) -> None:
    # Given: the legacy auto-save flag has stopped at the confirmation boundary.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()

    waiting = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
            auto_save_naver=True,
        )
    )

    # When: the operator sends the distinct confirmation request.
    saved = confirm_job(
        ConfirmationInput(
            tmp_path,
            waiting.run_id,
            "naver-draft-save",
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
            confirmation_nonce=str(
                json.loads(waiting.state_path.read_text(encoding="utf-8"))["confirmation_nonce"]
            ),
        )
    )

    # Then: exactly that confirmed transition performs the save.
    assert saved.status is RunStatus.DRAFT_SAVED
    assert naver.prepare_calls == 1
    assert naver.save_calls == 1


def test_uncertain_naver_save_blocks_preparation_renewal(
    tmp_path: Path,
) -> None:
    # Given: confirmation was recorded, but the save outcome is unknown.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = InterruptFirstSaveNaver()

    first = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
        )
    )
    first_state = json.loads(first.state_path.read_text(encoding="utf-8"))
    interrupted = confirm_job(
        ConfirmationInput(
            tmp_path,
            first.run_id,
            "naver-draft-save",
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
            confirmation_nonce=str(first_state["confirmation_nonce"]),
        )
    )
    assert interrupted.status is RunStatus.FAILED
    assert naver.save_calls == 0

    state_after_interruption = json.loads(
        interrupted.state_path.read_text(encoding="utf-8")
    )
    assert state_after_interruption["naver_save_outcome_uncertain"] is True
    with pytest.raises(ContractError, match="outcome is uncertain"):
        invalidate_naver_preparation(tmp_path, first.run_id)
    with pytest.raises(ContractError, match="outcome is uncertain"):
        _ = resume_job(
            RunnerRequest(
                root=tmp_path,
                job="",
                run_id=first.run_id,
                executor=FixtureExecutor(),
                notion_adapter=FixtureNotion(),
                naver_adapter=naver,
            )
        )
    assert naver.save_attempts == 1
    assert naver.save_calls == 0


def test_q2_failure_is_recorded_and_blocks_naver(tmp_path: Path) -> None:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        selection_context=DATE_CONTEXT,
        executor=FixtureExecutor(),
        notion_adapter=FailingQ2Notion(),
        naver_adapter=FixtureNaver(),
    )


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
    notion_event = next(item for item in events if item.get("stage") == "notion-rider")
    assert notion_event["quality"]["storage_integrity"] == "failed"
    assert notion_event["quality"]["external_call"] is True


def test_naver_gate_failure_prevents_prepare(tmp_path: Path) -> None:
    # Given: Q2 reports an artifact digest that is stale for the current manifest.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()

    # When: the pipeline reaches the Naver preparation boundary.

    result = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor(),
            notion_adapter=StaleDigestNotion(),
            naver_adapter=naver,
        )
    )

    # Then: fresh authorization fails before either browser mutation is invoked.
    assert result.status is RunStatus.FAILED
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0


def test_low_article_quality_blocks_naver_before_any_adapter_call(
    tmp_path: Path,
) -> None:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()

    result = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor(),
            notion_adapter=LowQualityNotion(),
            naver_adapter=naver,
        )
    )

    assert result.status is RunStatus.FAILED
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0
    events = [
        json.loads(line)
        for line in result.log_path.read_text(encoding="utf-8").splitlines()
    ]
    naver_event = next(item for item in events if item.get("stage") == "naver-rider")
    assert naver_event["error_type"] == "quality_failed"
    quality = naver_event["quality"]["article_quality"]
    assert isinstance(quality, dict)
    assert quality["article_quality_score"] == 63
    assert quality["cause_type"] == "topic_unsuitable"


def test_stale_confirmation_resume_revalidates_gate_before_save(
    tmp_path: Path,
) -> None:
    # Given: a valid preview whose persisted Q2 log is made stale before confirmation.
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()

    waiting = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
        )
    )
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
    result = confirm_job(
        ConfirmationInput(
            tmp_path,
            waiting.run_id,
            "naver-draft-save",
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
            confirmation_nonce=str(
                json.loads(waiting.state_path.read_text(encoding="utf-8"))["confirmation_nonce"]
            ),
        )
    )

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

    waiting = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
        )
    )
    naver.blog_id = "blog-other"

    # When: the operator confirms the stale target preview.
    result = confirm_job(
        ConfirmationInput(
            tmp_path,
            waiting.run_id,
            "naver-draft-save",
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
            confirmation_nonce=str(
                json.loads(waiting.state_path.read_text(encoding="utf-8"))["confirmation_nonce"]
            ),
        )
    )

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

    ready = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
        )
    )
    state = json.loads(ready.state_path.read_text(encoding="utf-8"))
    state["notion_roundtrip_digest"] = "sha256:" + "0" * 64
    _ = ready.state_path.write_text(json.dumps(state), encoding="utf-8")
    naver = CountingNaver()

    # When: the ready run resumes with a Naver adapter.
    result = resume_job(
        RunnerRequest(
            root=tmp_path,
            job="",
            run_id=ready.run_id,
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
        )
    )

    # Then: persisted Q2 is checked before any browser preparation or save.
    assert result.status is RunStatus.FAILED
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0


def test_q2_completion_pauses_before_naver_until_explicit_resume(
    tmp_path: Path,
) -> None:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        selection_context=DATE_CONTEXT,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=naver,
    )

    ready = run_job(request)

    assert ready.status is RunStatus.READY_FOR_NAVER
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0

    resumed = resume_job(
        RunnerRequest(
            root=tmp_path,
            job="",
            run_id=ready.run_id,
            resume=True,
            executor=FixtureExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
        )
    )

    assert resumed.status is RunStatus.AWAITING_USER_CONFIRMATION
    assert naver.prepare_calls == 1
    assert naver.save_calls == 0


def test_naver_preparation_rejects_input_different_from_q2_reviewed_copy(
    tmp_path: Path,
) -> None:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()
    result = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="fixture",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=MismatchedNaverInputExecutor(),
            notion_adapter=FixtureNotion(),
            naver_adapter=naver,
        )
    )

    assert result.status is RunStatus.FAILED
    assert "differs from the Q2-reviewed copy" in result.message
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0


def test_naver_preparation_requires_image_quality_records_for_current_images(
    tmp_path: Path,
) -> None:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        selection_context=DATE_CONTEXT,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=naver,
    )
    ready = run_job(request)
    quality = post_q2_image_review_path(tmp_path, ready.run_id)
    assert quality.is_file()
    quality.unlink()

    result = resume_job(replace(request, run_id=ready.run_id, resume=True))

    assert result.status in {RunStatus.BLOCKED, RunStatus.FAILED}
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("run_id", "RUN-other"),
        ("article_quality_report_digest", "sha256:" + "0" * 64),
        ("reviewed_at", NOW.isoformat()),
    ),
)
def test_naver_preparation_rejects_unbound_or_pre_q2_image_review(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = CountingNaver()
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
        now=NOW,
        selection_context=DATE_CONTEXT,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
        naver_adapter=naver,
    )
    ready = run_job(request)
    quality_path = post_q2_image_review_path(tmp_path, ready.run_id)
    records = [json.loads(line) for line in quality_path.read_text().splitlines()]
    records[0][field] = value
    _ = quality_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    result = resume_job(replace(request, run_id=ready.run_id, resume=True))

    assert result.status in {RunStatus.BLOCKED, RunStatus.FAILED}
    assert naver.prepare_calls == 0
    assert naver.save_calls == 0


def test_user_defined_topic_cannot_be_replaced_by_selector(tmp_path: Path) -> None:

    result = _run_through_q3_fixture(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            keyword="user-topic",
            now=NOW,
            selection_context=DATE_CONTEXT,
            executor=FixtureExecutor("replacement-topic"),
        )
    )

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
        selection_context=DATE_CONTEXT,
        executor=FixtureExecutor(),
    )


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
        selection_context=DATE_CONTEXT,
        executor=FixtureExecutor(),
        notion_adapter=FixtureNotion(),
    )


    # When: the actual pipeline executes through Notion Q2.
    result = run_job(request)
    state = json.loads(result.state_path.read_text(encoding="utf-8"))

    # Then: execution pauses after Q2 so the Q3 quality report can be recorded.
    assert result.status is RunStatus.READY_FOR_NAVER
    assert state["stages"]["notion-rider"] == "passed"
    assert state["stages"]["naver-rider"] == "pending"
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

    exit_code = runner_main(
        [
            "automation-runner",
            "run",
            "daily-generate",
            "--root",
            str(tmp_path),
            "--auto-topic",
            "--as-of-date",
            "2026-09-07",
            "--dry-run",
        ]
    )

    assert exit_code == 0
