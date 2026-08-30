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
from tools.runner_types import RunnerRequest, RunnerResult, RunStatus


def test_manual_payload_accepts_exactly_one_topic_source() -> None:
    user_defined = parse_manual_run_payload({"keyword": "테스트 주제"})
    auto_selected = parse_manual_run_payload({"auto_topic": True})
    assert (user_defined.keyword, user_defined.auto_topic) == ("테스트 주제", False)
    assert (auto_selected.keyword, auto_selected.auto_topic) == (None, True)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"keyword": "테스트", "auto_topic": True},
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
        parse_manual_run_payload({"auto_topic": True})
    )
    manager.close()
    result = manager.get(task.task_id)
    assert result is not None
    assert result.status == "completed"
    assert result.run_id == "RUN-manual"
    request = captured[0]
    assert request.dry_run is True
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
        parse_manual_run_payload({"auto_topic": True})
    )
    manager.close()
    result = manager.get(task.task_id)
    assert result is not None
    assert result.status == "completed"
    assert result.result_status == "local-only"
    assert result.message == "content generated; external storage pending"
    assert captured[0].dry_run is True


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
        parse_manual_run_payload({"keyword": "버튼 테스트"})
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
    task = manager.start(parse_manual_run_payload({"auto_topic": True}))
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
