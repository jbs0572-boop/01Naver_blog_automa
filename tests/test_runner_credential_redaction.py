from __future__ import annotations

from pathlib import Path

import pytest

from tools.codex_stage_executor import CodexStageExecutor
from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import ExternalWriteRequest
from tools.notion_keychain import NotionApiToken, NotionCredentialsUnavailable
from tools.runner_execution import run_job
from tools.runner_types import (
    RunExecutionContext,
    RunnerRequest,
    RunnerResult,
    RunStatus,
    StageExecutionContext,
    StageResult,
)


class FixtureNotion:
    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        _ = request
        return {}


class FixtureExecutor:
    def execute(self, context: StageExecutionContext) -> StageResult:
        _ = context
        raise AssertionError("captured runner must not execute a stage")


def _capture_default_executor(
    request: RunnerRequest,
    monkeypatch: pytest.MonkeyPatch,
    token_loader: list[int],
) -> CodexStageExecutor | FixtureExecutor:
    captured: list[CodexStageExecutor | FixtureExecutor] = []

    def load_token() -> NotionApiToken:
        token_loader.append(1)
        return NotionApiToken("opaque!notion-token?[]")

    def execute(context: RunExecutionContext) -> RunnerResult:
        executor = context.request.executor
        assert isinstance(executor, (CodexStageExecutor, FixtureExecutor))
        captured.append(executor)
        return RunnerResult(
            context.run_id,
            RunStatus.LOCAL_ONLY,
            context.state_path,
            context.log_path,
            (),
            "captured",
        )

    def configured(_root: Path) -> bool:
        return True

    def target(_root: Path) -> str:
        return "target"

    monkeypatch.setattr("tools.runner_execution.codex_project_is_configured", configured)
    monkeypatch.setattr("tools.runner_execution.configured_notion_target", target)
    monkeypatch.setattr("tools.runner_execution.load_notion_api_token", load_token)
    monkeypatch.setattr("tools.runner_execution.execute_run", execute)

    _ = run_job(request)

    assert len(captured) == 1
    return captured[0]


def test_live_daily_default_executor_loads_keychain_once_and_hides_repr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    executor = _capture_default_executor(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            auto_topic=True,
            notion_adapter=FixtureNotion(),
        ),
        monkeypatch,
        calls,
    )

    assert isinstance(executor, CodexStageExecutor)
    assert calls == [1]
    assert executor.sensitive_values == ("opaque!notion-token?[]",)
    assert "opaque!notion-token?[]" not in repr(executor)


@pytest.mark.parametrize(
    "candidate",
    (
        RunnerRequest(
            root=Path("."),
            job="daily-generate",
            auto_topic=True,
            dry_run=True,
            notion_adapter=FixtureNotion(),
        ),
        RunnerRequest(root=Path("."), job="weekly-improve"),
    ),
)
def test_non_live_default_executor_never_loads_keychain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate: RunnerRequest,
) -> None:
    calls: list[int] = []

    executor = _capture_default_executor(
        RunnerRequest(
            root=tmp_path,
            job=candidate.job,
            auto_topic=candidate.auto_topic,
            dry_run=candidate.dry_run,
            notion_adapter=candidate.notion_adapter,
        ),
        monkeypatch,
        calls,
    )

    assert isinstance(executor, CodexStageExecutor)
    assert calls == []
    assert executor.sensitive_values == ()


def test_initial_dry_run_strips_supplied_adapters_before_stage_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a dry-run with adapters that must remain completely unused.
    captured: list[RunnerRequest] = []
    keychain_calls: list[int] = []

    class ForbiddenNotion:
        def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
            _ = request
            raise AssertionError("dry-run must not call the supplied Notion adapter")

    class ForbiddenNaver:
        @property
        def target_blog_id(self) -> str:
            raise AssertionError("dry-run must not inspect the supplied Naver adapter")

        def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
            _ = (title, body, artifact_digest)
            raise AssertionError("dry-run must not prepare a Naver draft")

        def save(self, title: str, artifact_digest: str) -> JSONMap:
            _ = (title, artifact_digest)
            raise AssertionError("dry-run must not save a Naver draft")

    def forbidden_keychain() -> NotionApiToken:
        keychain_calls.append(1)
        raise AssertionError("dry-run must not load Keychain credentials")

    def capture(context: RunExecutionContext) -> RunnerResult:
        captured.append(context.request)
        return RunnerResult(
            context.run_id,
            RunStatus.LOCAL_ONLY,
            context.state_path,
            context.log_path,
            (),
            "captured",
        )

    def configured(_root: Path) -> bool:
        return True

    monkeypatch.setattr("tools.runner_execution.codex_project_is_configured", configured)
    monkeypatch.setattr("tools.runner_execution.load_notion_api_token", forbidden_keychain)
    monkeypatch.setattr("tools.runner_execution.execute_run", capture)

    # When: the initial request crosses the runner boundary.
    _ = run_job(RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        auto_topic=True,
        dry_run=True,
        notion_adapter=ForbiddenNotion(),
        naver_adapter=ForbiddenNaver(),
    ))

    # Then: no external capability reaches the stage executor or Keychain loader.
    assert keychain_calls == []
    assert len(captured) == 1
    assert captured[0].notion_adapter is None
    assert captured[0].naver_adapter is None


def test_explicit_executor_never_loads_keychain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    fixture = FixtureExecutor()

    executor = _capture_default_executor(
        RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            auto_topic=True,
            notion_adapter=FixtureNotion(),
            executor=fixture,
        ),
        monkeypatch,
        calls,
    )

    assert executor is fixture
    assert calls == []


def test_live_daily_keychain_failure_is_stable_and_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> NotionApiToken:
        raise NotionCredentialsUnavailable()

    def configured(_root: Path) -> bool:
        return True

    def target(_root: Path) -> str:
        return "target"

    monkeypatch.setattr("tools.runner_execution.codex_project_is_configured", configured)
    monkeypatch.setattr("tools.runner_execution.configured_notion_target", target)
    monkeypatch.setattr("tools.runner_execution.load_notion_api_token", unavailable)

    with pytest.raises(ContractError) as captured:
        _ = run_job(RunnerRequest(
            root=tmp_path,
            job="daily-generate",
            auto_topic=True,
            notion_adapter=FixtureNotion(),
        ))

    assert str(captured.value) == "notion_credentials_unavailable"
