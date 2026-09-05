from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.dashboard_manual_run import (
    ManualRunContext,
    ManualRunDependencies,
    ManualRunManager,
    parse_manual_run_payload,
)
from tools.external_adapter import ExternalWriteRequest
from tools.runner_types import (
    RunnerRequest,
    RunnerResult,
    RunStatus,
)


def auto_payload() -> JSONMap:
    return {
        "auto_topic": True,
        "selection_context": {
            "category": "생활정보",
            "audience": "30~40대 직장인",
            "publish_purpose": "검색 유입용 정보글",
            "as_of_date": "2026-09-01",
            "timezone": "Asia/Seoul",
        },
    }


def test_manual_payload_accepts_exactly_one_topic_source() -> None:
    user_defined = parse_manual_run_payload({"keyword": "테스트 주제", "as_of_date": "2026-09-01"})
    auto_selected = parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-01"})
    assert (user_defined.keyword, user_defined.auto_topic) == ("테스트 주제", False)
    assert (auto_selected.keyword, auto_selected.auto_topic) == (None, True)


def test_manual_payload_accepts_keyword_or_auto_topic_with_kst_date() -> None:
    user_defined = parse_manual_run_payload(
        {"keyword": "지정 키워드", "as_of_date": "2026-09-01"}
    )
    auto_selected = parse_manual_run_payload(
        {"auto_topic": True, "as_of_date": "2026-09-01"}
    )

    assert user_defined.keyword == "지정 키워드"
    assert user_defined.selection_context is not None
    assert user_defined.selection_context.as_of_date == "2026-09-01"
    assert auto_selected.auto_topic is True
    assert auto_selected.selection_context is not None
    assert auto_selected.selection_context.as_of_date == "2026-09-01"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"keyword": "테스트", "auto_topic": True},
        {"keyword": "테스트"},
        {"auto_topic": True},
        {"keyword": "테스트", "as_of_date": "2026-09-01", "extra": "x"},
        {"auto_topic": False},
        {"keyword": ""},
        {"mode": "legacy", "auto_topic": True},
        {"keyword": "테스트", "unexpected": True},
    ],
)
def test_manual_payload_rejects_noncanonical_topic_sources(payload: JSONMap) -> None:
    with pytest.raises(ContractError):
        _ = parse_manual_run_payload(payload)


def test_manual_manager_runs_with_external_writes_disabled(tmp_path: Path) -> None:
    captured: list[RunnerRequest] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            "RUN-manual",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "fixture completed",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False, False), ManualRunDependencies(runner)
    )
    task = manager.start(
        parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-01"})
    )
    manager.close()
    result = manager.get(task.task_id)
    assert result is not None
    assert result.status == "completed"
    assert result.run_id == "RUN-manual"
    request = captured[0]
    assert request.dry_run is False
    assert request.auto_topic is True


def test_dashboard_manager_runs_local_pipeline_without_external_adapters(
    tmp_path: Path,
) -> None:
    captured: list[RunnerRequest] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            "RUN-local",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "content generated; external storage pending",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False, False), ManualRunDependencies(runner)
    )
    task = manager.start(
        parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-01"})
    )
    manager.close()
    result = manager.get(task.task_id)
    assert result is not None
    assert result.status == "completed"
    assert result.result_status == "local-only"
    assert result.message == "content generated; external storage pending"
    assert captured[0].dry_run is False


def test_dashboard_manager_starts_auto_selection_without_human_context(
    tmp_path: Path,
) -> None:
    captured: list[RunnerRequest] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            "RUN-local",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "local-only",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False, False), ManualRunDependencies(runner)
    )
    task = manager.start(
        parse_manual_run_payload(auto_payload())
    )
    manager.close()

    assert manager.get(task.task_id) is not None
    assert captured[0].selection_context is not None
    assert captured[0].selection_context.as_of_date == "2026-09-01"


def test_dashboard_initial_run_strips_supplied_adapters_before_calling_runner(
    tmp_path: Path,
) -> None:
    captured: list[RunnerRequest] = []

    class ForbiddenNotion:
        def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
            _ = request
            raise AssertionError("dashboard dry-run must not call the Notion adapter")

    class ForbiddenNaver:
        @property
        def target_blog_id(self) -> str:
            raise AssertionError("dashboard dry-run must not inspect the Naver adapter")

        def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
            _ = (title, body, artifact_digest)
            raise AssertionError("dashboard dry-run must not prepare a Naver draft")

        def save(self, title: str, artifact_digest: str) -> JSONMap:
            _ = (title, artifact_digest)
            raise AssertionError("dashboard dry-run must not save a Naver draft")

    def runner(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            "RUN-local",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "local-only",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False, False),
        ManualRunDependencies(
            runner,
            notion_adapter=ForbiddenNotion(),
            naver_adapter=ForbiddenNaver(),
        ),
    )

    task = manager.start(parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-01"}))
    manager.close()

    # Then: the runner receives no external capability.
    result = manager.get(task.task_id)
    assert result is not None
    assert result.status == "completed"
    assert len(captured) == 1
    assert captured[0].dry_run is False
    assert captured[0].notion_adapter is None
    assert captured[0].naver_adapter is None


def test_dashboard_external_resume_injects_adapters_after_initial_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[RunnerRequest] = []
    resumed: list[RunnerRequest] = []

    class Notion:
        def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
            _ = request
            return {}

    class Naver:
        @property
        def target_blog_id(self) -> str:
            return "blog-live"

        def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
            _ = (title, body, artifact_digest)
            return {}

        def save(self, title: str, artifact_digest: str) -> JSONMap:
            _ = (title, artifact_digest)
            return {}

    def result(request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            "RUN-live",
            RunStatus.LOCAL_ONLY,
            request.root / "state.json",
            request.root / "log.jsonl",
            (),
            "local-only",
        )

    def runner(request: RunnerRequest) -> RunnerResult:
        started.append(request)
        return result(request)

    def resume(request: RunnerRequest) -> RunnerResult:
        resumed.append(request)
        return result(request)

    monkeypatch.setattr("tools.dashboard_manual_run.resume_job", resume)
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `pinned-live-target`\n", encoding="utf-8"
    )
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False, True),
        ManualRunDependencies(runner, notion_adapter=Notion(), naver_adapter=Naver()),
    )
    task = manager.start(
        parse_manual_run_payload({"keyword": "live-test", "as_of_date": "2026-09-01"})
    )
    manager.close()
    _ = manager.continue_external(task.task_id)

    assert started[0].dry_run is False
    assert started[0].notion_target_id == "pinned-live-target"
    assert started[0].notion_adapter is None
    assert started[0].naver_adapter is None
    assert resumed[0].dry_run is False
    assert resumed[0].notion_adapter is not None
    assert resumed[0].naver_adapter is not None


def test_dashboard_dry_continue_external_strips_supplied_adapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[RunnerRequest] = []
    resumed: list[RunnerRequest] = []

    class ForbiddenNotion:
        def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
            _ = request
            raise AssertionError("dashboard dry-run must not call the Notion adapter")

    class ForbiddenNaver:
        @property
        def target_blog_id(self) -> str:
            raise AssertionError("dashboard dry-run must not inspect the Naver adapter")

        def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
            _ = (title, body, artifact_digest)
            raise AssertionError("dashboard dry-run must not prepare a Naver draft")

        def save(self, title: str, artifact_digest: str) -> JSONMap:
            _ = (title, artifact_digest)
            raise AssertionError("dashboard dry-run must not save a Naver draft")

    def result(request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            "RUN-local",
            RunStatus.LOCAL_ONLY,
            request.root / "state",
            request.root / "log",
            (),
            "local-only",
        )

    def runner(request: RunnerRequest) -> RunnerResult:
        started.append(request)
        return result(request)

    def resume(request: RunnerRequest) -> RunnerResult:
        resumed.append(request)
        return result(request)

    monkeypatch.setattr("tools.dashboard_manual_run.resume_job", resume)
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False, False),
        ManualRunDependencies(
            runner,
            notion_adapter=ForbiddenNotion(),
            naver_adapter=ForbiddenNaver(),
        ),
    )
    task = manager.start(parse_manual_run_payload(auto_payload()))
    manager.close()
    _ = manager.continue_external(task.task_id)

    assert len(started) == 1
    assert len(resumed) == 1
    assert resumed[0].dry_run is True
    assert resumed[0].notion_adapter is None
    assert resumed[0].naver_adapter is None


def test_external_storage_button_stays_pending_without_adapters(tmp_path: Path) -> None:
    def runner(_request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            "RUN-local",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "content workflow completed; external storage is pending",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False, False), ManualRunDependencies(runner)
    )
    task = manager.start(
        parse_manual_run_payload({"keyword": "버튼 테스트", "as_of_date": "2026-09-01"})
    )
    manager.close()
    updated = manager.continue_external(task.task_id)
    assert updated.status == "completed"
    assert updated.result_status == "local-only"
    assert updated.message == "외부 저장 대기 · Notion/Naver 연결이 필요합니다"


def test_awaiting_run_exposes_complete_naver_confirmation_preview(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "awaiting.json"

    def runner(_request: RunnerRequest) -> RunnerResult:
        _ = state_path.write_text(
            json.dumps(
                {
                    "target_blog_id": "blog-fixture",
                    "naver_title": "확인할 제목",
                    "artifact_digest": "sha256:" + "a" * 64,
                    "artifact_paths": [
                        "final/topic.md",
                        "assets/topic/body.png",
                        "assets/topic/thumbnail.png",
                    ],
                }
            ),
            encoding="utf-8",
        )
        return RunnerResult(
            "RUN-awaiting",
            RunStatus.AWAITING_USER_CONFIRMATION,
            state_path,
            tmp_path / "log",
            (),
            "awaiting confirmation",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False, True), ManualRunDependencies(runner)
    )
    task = manager.start(parse_manual_run_payload(auto_payload()))
    manager.close()
    result = manager.get(task.task_id)

    assert result is not None
    preview = result.as_json()["confirmation_preview"]
    assert preview == {
        "action": "naver-draft-save",
        "target_blog_id": "blog-fixture",
        "title": "확인할 제목",
        "images": ["assets/topic/body.png", "assets/topic/thumbnail.png"],
        "artifact_digest": "sha256:" + "a" * 64,
    }
