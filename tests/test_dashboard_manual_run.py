from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.test_dashboard_manual_store import persist_legacy_pending_batch
from tests.test_runner_prd import FixtureExecutor
from tools.contract_types import ContractError, JSONMap
from tools.dashboard_manual_batch import new_batch
from tools.dashboard_manual_models import (
    AUTO_BATCH_SIZE,
    ConfirmationPreview,
    ManualActionView,
    ManualActiveActionView,
    ManualBatchView,
    ManualRunView,
    ManualSnapshotView,
)
from tools.dashboard_manual_actions import execute_child_action
from tools.dashboard_manual_run import (
    ManualRunContext,
    ManualRunDependencies,
    ManualRunManager,
    is_stale_recovery,
    parse_manual_run_payload,
)
from tools.dashboard_manual_store import ManualBatchStore
from tools.external_adapter import ExternalWriteRequest
from tools.log_contract import read_events
from tools.runner_execution import run_job
from tools.runner_state import atomic_write_json, read_state, state_paths
from tools.runner_types import (
    RunnerRequest,
    RunnerResult,
    RunStatus,
)


def auto_payload() -> JSONMap:
    return {
        "auto_topic": True,
        "as_of_date": "2026-09-01",
    }


def test_manual_payload_accepts_exactly_one_topic_source() -> None:
    user_defined = parse_manual_run_payload(
        {"keyword": "테스트 주제", "as_of_date": "2026-09-01"}
    )
    auto_selected = parse_manual_run_payload(
        {"auto_topic": True, "as_of_date": "2026-09-01"}
    )
    assert (user_defined.keyword, user_defined.auto_topic) == ("테스트 주제", False)
    assert (auto_selected.keyword, auto_selected.auto_topic) == (None, True)


def test_recovery_marks_queue_item_stale_after_fifteen_minutes() -> None:
    now = datetime(2026, 9, 12, 3, 0, tzinfo=UTC)
    assert is_stale_recovery("2026-09-12T02:44:59+00:00", now=now)
    assert not is_stale_recovery("2026-09-12T02:45:01+00:00", now=now)


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


def test_auto_payload_has_no_client_count_and_builds_one_child_intent() -> None:
    # Given
    payload: JSONMap = {"auto_topic": True, "as_of_date": "2026-09-07"}

    # When
    request = parse_manual_run_payload(payload)

    # Then
    assert request.auto_topic is True
    assert request.keyword is None
    assert request.child_count == AUTO_BATCH_SIZE == 1


def test_user_payload_builds_one_child_intent() -> None:
    # Given
    payload: JSONMap = {"keyword": "지정 주제", "as_of_date": "2026-09-07"}

    # When
    request = parse_manual_run_payload(payload)

    # Then
    assert request.auto_topic is False
    assert request.keyword == "지정 주제"
    assert request.child_count == 1


@pytest.mark.parametrize(
    "payload",
    [
        {
            "auto_topic": True,
            "as_of_date": "2026-09-07",
            "count": 3,
        },
        {
            "auto_topic": True,
            "as_of_date": "2026-09-07",
            "selection_context": {},
        },
    ],
)
def test_manual_payload_rejects_selection_context_and_count(payload: JSONMap) -> None:
    # Given / When / Then
    with pytest.raises(ContractError):
        _ = parse_manual_run_payload(payload)


def test_manual_batch_view_serializes_ordered_children_and_next_action() -> None:
    # Given
    snapshot = ManualSnapshotView(
        "CAPTURE-a1b2c3d4e5f6",
        "metadata/creator-advisor/2026-09-07/CAPTURE-a1b2c3d4e5f6.json",
        None,
    )
    first = ManualRunView(
        "BATCH-a1b2c3d4e5f6-01",
        "completed",
        "2026-09-07T00:00:00+00:00",
        "2026-09-07T00:01:00+00:00",
        child_id="CHILD-01",
        slot=1,
        keyword="첫 주제",
        next_action=ManualActionView("external", "nonce-first"),
    )
    second = ManualRunView(
        "BATCH-a1b2c3d4e5f6-02",
        "queued",
        "2026-09-07T00:00:00+00:00",
        "2026-09-07T00:01:00+00:00",
        child_id="CHILD-02",
        slot=2,
    )
    batch = ManualBatchView(
        "BATCH-a1b2c3d4e5f6",
        "running",
        "auto_selected",
        "2026-09-07",
        "2026-09-07T00:00:00+00:00",
        "2026-09-07T00:01:00+00:00",
        snapshot,
        (first, second),
    )

    # When
    serialized = batch.as_json()

    # Then
    assert serialized["batch_id"] == "BATCH-a1b2c3d4e5f6"
    assert serialized["children"] == [first.as_json(), second.as_json()]
    assert first.as_json()["next_action"] == {
        "kind": "external",
        "nonce": "nonce-first",
    }
    assert "confirmation_preview" not in serialized


def test_manual_manager_runs_with_external_writes_disabled(tmp_path: Path) -> None:
    captured: list[RunnerRequest] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            str(request.run_id),
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "fixture completed",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(runner)
    )
    task = manager.start(
        parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-01"})
    )
    manager.close()
    batch = manager.get(task.batch_id)
    result = batch.children[0] if batch is not None else None
    assert result is not None
    assert result.status == "completed"
    assert result.run_id == task.children[0].run_id
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
            str(request.run_id),
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "content generated; external storage pending",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(runner)
    )
    task = manager.start(
        parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-01"})
    )
    manager.close()
    batch = manager.get(task.batch_id)
    result = batch.children[0] if batch is not None else None
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
            str(request.run_id),
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "local-only",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(runner)
    )
    task = manager.start(parse_manual_run_payload(auto_payload()))
    manager.close()

    assert manager.get(task.batch_id) is not None
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
            str(request.run_id),
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "local-only",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(
            runner,
            notion_adapter=ForbiddenNotion(),
            naver_adapter=ForbiddenNaver(),
        ),
    )

    task = manager.start(
        parse_manual_run_payload({"auto_topic": True, "as_of_date": "2026-09-01"})
    )
    manager.close()

    # Then: the runner receives no external capability.
    batch = manager.get(task.batch_id)
    result = batch.children[0] if batch is not None else None
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
            str(request.run_id),
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

    monkeypatch.setattr("tools.dashboard_manual_actions.resume_job", resume)
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `pinned-live-target`\n", encoding="utf-8"
    )
    manager = ManualRunManager(
        ManualRunContext(tmp_path, True),
        ManualRunDependencies(runner, notion_adapter=Notion(), naver_adapter=Naver()),
    )
    task = manager.start(
        parse_manual_run_payload({"keyword": "live-test", "as_of_date": "2026-09-01"})
    )
    manager.close()
    _ = manager.continue_external(task.children[0].task_id)

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
            str(request.run_id),
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

    monkeypatch.setattr("tools.dashboard_manual_actions.resume_job", resume)
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(
            runner,
            notion_adapter=ForbiddenNotion(),
            naver_adapter=ForbiddenNaver(),
        ),
    )
    task = manager.start(parse_manual_run_payload(auto_payload()))
    manager.close()
    _ = manager.continue_external(task.children[0].task_id)

    assert len(started) == 1
    assert len(resumed) == 1
    assert resumed[0].dry_run is True
    assert resumed[0].notion_adapter is None
    assert resumed[0].naver_adapter is None


def test_external_storage_button_stays_pending_without_adapters(tmp_path: Path) -> None:
    def runner(_request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            str(_request.run_id),
            RunStatus.LOCAL_ONLY,
            tmp_path / "state",
            tmp_path / "log",
            (),
            "content workflow completed; external storage is pending",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(runner)
    )
    task = manager.start(
        parse_manual_run_payload({"keyword": "버튼 테스트", "as_of_date": "2026-09-01"})
    )
    manager.close()
    updated = manager.continue_external(task.children[0].task_id)
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
            str(_request.run_id),
            RunStatus.AWAITING_USER_CONFIRMATION,
            state_path,
            tmp_path / "log",
            (),
            "awaiting confirmation",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, True), ManualRunDependencies(runner)
    )
    task = manager.start(parse_manual_run_payload(auto_payload()))
    manager.close()
    batch = manager.get(task.batch_id)
    result = batch.children[0] if batch is not None else None

    assert result is not None
    preview = result.as_json()["confirmation_preview"]
    assert preview == {
        "action": "naver-draft-save",
        "target_blog_id": "blog-fixture",
        "title": "확인할 제목",
        "images": ["assets/topic/body.png", "assets/topic/thumbnail.png"],
        "artifact_digest": "sha256:" + "a" * 64,
    }


def _selection_runner(
    root: Path,
    calls: list[RunnerRequest],
    *,
    fail_first_selector: bool = False,
) -> Callable[[RunnerRequest], RunnerResult]:
    keywords = ("첫 주제", "둘째 주제", "셋째 주제")

    def runner(request: RunnerRequest) -> RunnerResult:
        calls.append(request)
        slot = request.selection_context.batch_slot if request.selection_context else 1
        assert slot is not None
        state_path = root / ".automation" / "state" / f"{request.run_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state: JSONMap = {
            "run_id": request.run_id,
            "status": "failed" if fail_first_selector and slot == 1 else "local-only",
            "message": "selector failed"
            if fail_first_selector and slot == 1
            else "local-only",
            "stages": {
                "topic-selector": "failed"
                if fail_first_selector and slot == 1
                else "passed"
            },
        }
        if not (fail_first_selector and slot == 1):
            context = request.selection_context
            assert context is not None
            state.update(
                {
                    "keyword": keywords[slot - 1],
                    "capture_id": context.capture_id,
                    "selection_snapshot_path": context.snapshot_path,
                    "selection_snapshot_sha256": "sha256:" + "a" * 64,
                }
            )
        _ = state_path.write_text(json.dumps(state), encoding="utf-8")
        return RunnerResult(
            str(request.run_id),
            RunStatus.FAILED
            if fail_first_selector and slot == 1
            else RunStatus.LOCAL_ONLY,
            state_path,
            root / ".automation" / "logs" / f"{request.run_id}.jsonl",
            (),
            str(state["message"]),
        )

    return runner


def test_auto_start_preallocates_exactly_one_child(tmp_path: Path) -> None:
    calls: list[RunnerRequest] = []
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(_selection_runner(tmp_path, calls)),
    )

    batch = manager.start(parse_manual_run_payload(auto_payload()))

    assert len(batch.children) == 1
    assert [child.slot for child in batch.children] == [1]
    assert len({child.child_id for child in batch.children}) == 1
    assert len({child.run_id for child in batch.children}) == 1
    manager.close()


def test_user_start_preallocates_one_child_and_preserves_keyword(
    tmp_path: Path,
) -> None:
    calls: list[RunnerRequest] = []
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(_selection_runner(tmp_path, calls)),
    )

    batch = manager.start(
        parse_manual_run_payload({"keyword": "지정 주제", "as_of_date": "2026-09-01"})
    )

    assert len(batch.children) == 1
    assert batch.children[0].requested_keyword == "지정 주제"
    manager.close()


def test_dashboard_daily_path_persists_feedback_pins_in_state_and_log(
    tmp_path: Path,
) -> None:
    # Given: a dashboard user-topic run using the shared daily runner.
    config = tmp_path / "config"
    config.mkdir()
    project_root = Path(__file__).resolve().parents[1]
    _ = shutil.copy(project_root / "config/topic-feedback-rollout.json", config)
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(run_job, executor=FixtureExecutor()),
    )

    # When: the dashboard child completes locally.
    batch = manager.start(
        parse_manual_run_payload(
            {"keyword": "대시보드 주제", "as_of_date": "2026-09-09"}
        )
    )
    manager.close()

    # Then: its persisted state and every stage log share all immutable pins.
    child = batch.children[0]
    assert child.run_id is not None
    state_path = tmp_path / ".automation/state" / f"{child.run_id}.json"
    log_path = tmp_path / ".automation/logs" / f"{child.run_id}.jsonl"
    state = read_state(state_path)
    events = [
        item for item in read_events(log_path) if item.get("event_type") == "stage"
    ]
    keys = ("score_version", "score_config_digest", "feedback_manifest_digest")
    expected = tuple(state[key] for key in keys)
    assert len(events) == 7
    assert all(tuple(item[key] for key in keys) == expected for item in events)


def test_startup_keeps_legacy_three_child_batch_read_only(tmp_path: Path) -> None:
    # Given: a persisted batch from the retired three-child auto envelope.
    batch = persist_legacy_pending_batch(tmp_path)
    store = ManualBatchStore(tmp_path)
    path = store.store_path(batch.batch_id)
    original = path.read_bytes()
    calls: list[RunnerRequest] = []

    # When: a current dashboard manager starts and scans durable history.
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(_selection_runner(tmp_path, calls)),
    )
    manager.close()

    # Then: history remains readable and byte-stable without runner promotion.
    assert calls == []
    assert path.read_bytes() == original
    assert manager.get(batch.batch_id) == batch


def test_startup_does_not_rewrite_stale_legacy_children(tmp_path: Path) -> None:
    batch = persist_legacy_pending_batch(tmp_path)
    stale = replace(
        batch,
        updated_at="2026-09-01T00:00:00+00:00",
        children=tuple(
            replace(child, updated_at="2026-09-01T00:00:00+00:00")
            for child in batch.children
        ),
    )
    store = ManualBatchStore(tmp_path)
    store.save(stale)
    path = store.store_path(stale.batch_id)
    original = path.read_bytes()
    calls: list[RunnerRequest] = []

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(_selection_runner(tmp_path, calls)),
    )
    manager.close()

    assert calls == []
    assert path.read_bytes() == original
    assert manager.get(stale.batch_id) == stale


def test_startup_ignores_misleading_legacy_active_action_across_restarts(
    tmp_path: Path,
) -> None:
    batch = persist_legacy_pending_batch(tmp_path)
    active = ManualActiveActionView(
        "initial",
        "OPERATION-legacy",
        "sha256:" + "a" * 64,
        "2026-09-13T00:00:00+00:00",
        "running",
    )
    misleading = replace(
        batch,
        children=(replace(batch.children[0], active_action=active), *batch.children[1:]),
    )
    store = ManualBatchStore(tmp_path)
    store.save(misleading)
    path = store.store_path(misleading.batch_id)
    original = path.read_bytes()
    calls: list[RunnerRequest] = []

    for _ in range(2):
        manager = ManualRunManager(
            ManualRunContext(tmp_path, False),
            ManualRunDependencies(_selection_runner(tmp_path, calls)),
        )
        manager.close()

    assert calls == []
    assert path.read_bytes() == original
    assert store.get(misleading.batch_id) == misleading


def test_startup_recovery_clears_confirm_and_preserves_uncertain_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = parse_manual_run_payload(
        {"keyword": "재시작 확인", "as_of_date": "2026-09-29"}
    )
    batch = new_batch(request, tmp_path)
    run_id = "RUN-confirm-recovery"
    state_path, _, _ = state_paths(tmp_path, run_id)
    atomic_write_json(
        state_path,
        {
            "run_id": run_id,
            "naver_save_outcome_uncertain": True,
        },
    )
    active = ManualActiveActionView(
        "confirm",
        "OPERATION-confirm",
        "sha256:" + "a" * 64,
        datetime.now(UTC).isoformat(),
        "running",
    )
    child = replace(
        batch.children[0],
        status="completed",
        result_status=RunStatus.AWAITING_USER_CONFIRMATION.value,
        run_id=run_id,
        message="awaiting confirmation",
        next_action=ManualActionView("confirm", "confirmation-nonce"),
        active_action=active,
        updated_at=datetime.now(UTC).isoformat(),
    )
    store = ManualBatchStore(tmp_path)
    store.save(replace(batch, children=(child,)))
    invalidated: list[str] = []
    recovered_actions: list[str] = []

    def invalidate(_root: Path, invalid_run_id: str) -> None:
        invalidated.append(invalid_run_id)

    def recover(*_args: object) -> ManualRunView:
        recovered_actions.append("called")
        return child

    def runner(_request: RunnerRequest) -> RunnerResult:
        raise AssertionError("startup recovery must not run a new workflow")

    class ForbiddenNaver:
        @property
        def target_blog_id(self) -> str:
            raise AssertionError("uncertain save must not start a new Naver action")

        def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
            _ = (title, body, artifact_digest)
            raise AssertionError("uncertain save must not be prepared again")

        def save(self, title: str, artifact_digest: str) -> JSONMap:
            _ = (title, artifact_digest)
            raise AssertionError("uncertain save must not be replayed")

    monkeypatch.setattr("tools.dashboard_manual_run.invalidate_naver_preparation", invalidate)
    monkeypatch.setattr("tools.dashboard_manual_run.recover_child_action", recover)
    manager = ManualRunManager(
        ManualRunContext(tmp_path, True),
        ManualRunDependencies(runner, naver_adapter=ForbiddenNaver()),
    )
    manager.close()
    settled = manager.get(batch.batch_id)

    assert settled is not None
    recovered = settled.children[0]
    assert recovered.status == "failed"
    assert recovered.result_status == RunStatus.FAILED.value
    assert recovered.retryable is False
    assert recovered.next_action is None
    assert recovered.active_action is None
    assert recovered.confirmation_preview is None
    assert recovered.message is not None
    assert "수동 대조" in recovered.message
    assert invalidated == []
    assert recovered_actions == []


def test_confirm_failure_with_uncertain_save_does_not_offer_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = parse_manual_run_payload(
        {"keyword": "저장 결과 불확실", "as_of_date": "2026-09-29"}
    )
    batch = new_batch(request, tmp_path)
    run_id = "RUN-uncertain-save"
    state_path, log_path, _ = state_paths(tmp_path, run_id)
    atomic_write_json(
        state_path,
        {
            "run_id": run_id,
            "naver_save_outcome_uncertain": True,
        },
    )
    preview = ConfirmationPreview(
        action="naver-draft-save",
        target_blog_id="blog-fixture",
        title="확인할 제목",
        images=("assets/topic/image-01.png",),
        artifact_digest="sha256:" + "a" * 64,
    )
    child = replace(
        batch.children[0],
        status="completed",
        run_id=run_id,
        result_status=RunStatus.AWAITING_USER_CONFIRMATION.value,
        confirmation_preview=preview,
        next_action=ManualActionView("confirm", "nonce"),
    )
    failed = RunnerResult(
        run_id,
        RunStatus.FAILED,
        state_path,
        log_path,
        (),
        "save outcome is uncertain",
    )
    monkeypatch.setattr("tools.dashboard_manual_actions.confirm_job", lambda _input: failed)

    def runner(_request: RunnerRequest) -> RunnerResult:
        raise AssertionError("failed confirmation must not launch a new run")

    updated = execute_child_action(
        ManualRunContext(tmp_path, True),
        ManualRunDependencies(runner),
        child,
        "confirm",
    )

    assert updated.status == "failed"
    assert updated.result_status == RunStatus.FAILED.value
    assert updated.retryable is False
    assert updated.next_action is None
    assert updated.message is not None
    assert "수동 대조" in updated.message


def test_startup_recovery_clears_discarded_confirmation_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = parse_manual_run_payload(
        {"keyword": "확인 갱신", "as_of_date": "2026-09-29"}
    )
    batch = new_batch(request, tmp_path)
    run_id = "RUN-confirm-renewal"
    state_path, _, _ = state_paths(tmp_path, run_id)
    atomic_write_json(
        state_path,
        {
            "run_id": run_id,
            "naver_save_outcome_uncertain": False,
        },
    )
    active = ManualActiveActionView(
        "confirm",
        "OPERATION-confirm",
        "sha256:" + "b" * 64,
        datetime.now(UTC).isoformat(),
        "running",
    )
    child = replace(
        batch.children[0],
        status="completed",
        result_status=RunStatus.AWAITING_USER_CONFIRMATION.value,
        run_id=run_id,
        message="awaiting confirmation",
        next_action=ManualActionView("confirm", "confirmation-nonce"),
        active_action=active,
        updated_at=datetime.now(UTC).isoformat(),
    )
    store = ManualBatchStore(tmp_path)
    store.save(replace(batch, children=(child,)))
    def invalidate(_root: Path, _run_id: str) -> None:
        return

    monkeypatch.setattr(
        "tools.dashboard_manual_run.invalidate_naver_preparation", invalidate
    )
    recovered_actions: list[str] = []

    def recover(*_args: object) -> ManualRunView:
        recovered_actions.append("called")
        return child

    def runner(_request: RunnerRequest) -> RunnerResult:
        raise AssertionError("startup recovery must wait for external continuation")

    class ForbiddenNaver:
        @property
        def target_blog_id(self) -> str:
            raise AssertionError("recovery must not inspect the browser adapter")

        def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
            _ = (title, body, artifact_digest)
            raise AssertionError("recovery must not prepare until requested")

        def save(self, title: str, artifact_digest: str) -> JSONMap:
            _ = (title, artifact_digest)
            raise AssertionError("recovery must not save until confirmed")

    monkeypatch.setattr("tools.dashboard_manual_run.recover_child_action", recover)
    manager = ManualRunManager(
        ManualRunContext(tmp_path, True),
        ManualRunDependencies(runner, naver_adapter=ForbiddenNaver()),
    )
    manager.close()
    settled = manager.get(batch.batch_id)

    assert settled is not None
    recovered = settled.children[0]
    assert recovered.result_status == RunStatus.LOCAL_ONLY.value
    assert recovered.next_action is not None
    assert recovered.next_action.kind == "external"
    assert recovered.active_action is None
    assert recovered_actions == []


def test_startup_resumes_fresh_one_child_batch_exactly_once(
    tmp_path: Path,
) -> None:
    batch = new_batch(parse_manual_run_payload(auto_payload()), tmp_path)
    ManualBatchStore(tmp_path).save(batch)
    calls: list[RunnerRequest] = []
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(_selection_runner(tmp_path, calls)),
    )
    manager.close()
    settled = manager.get(batch.batch_id)

    assert settled is not None
    assert len(calls) == 1
    assert len(settled.children) == 1
    assert settled.children[0].result_status == RunStatus.LOCAL_ONLY.value


def test_child_rejects_runner_result_for_different_preallocated_run_id(
    tmp_path: Path,
) -> None:
    def runner(_request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            "RUN-wrong",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state.json",
            tmp_path / "log.jsonl",
            (),
            "wrong identity",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(runner)
    )
    batch = manager.start(
        parse_manual_run_payload({"keyword": "지정 주제", "as_of_date": "2026-09-01"})
    )
    manager.close()
    settled = manager.get(batch.batch_id)

    assert settled is not None
    assert settled.children[0].status == "failed"
    assert settled.children[0].error == "ContractError"


def test_initial_dashboard_runner_cannot_synthesize_draft_saved(
    tmp_path: Path,
) -> None:
    captured: list[RunnerRequest] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        captured.append(request)
        return RunnerResult(
            str(request.run_id),
            RunStatus.DRAFT_SAVED,
            tmp_path / "state.json",
            tmp_path / "log.jsonl",
            (),
            "synthetic save",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(runner)
    )
    batch = manager.start(parse_manual_run_payload(auto_payload()))
    manager.close()
    settled = manager.get(batch.batch_id)

    assert captured[0].auto_save_naver is False
    assert settled is not None
    assert settled.children[0].status == "failed"
    assert settled.children[0].result_status != RunStatus.DRAFT_SAVED.value
    assert settled.children[0].error == "ContractError"


def test_injected_local_runner_completes_one_child_without_adapters(
    tmp_path: Path,
) -> None:
    def local_runner(request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            request.run_id or "RUN-fixture",
            RunStatus.LOCAL_ONLY,
            tmp_path / "state.json",
            tmp_path / "log.jsonl",
            (),
            "fixture completed",
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(local_runner)
    )
    batch = manager.start(parse_manual_run_payload(auto_payload()))
    manager.close()
    settled = manager.get(batch.batch_id)

    assert settled is not None
    assert settled.status == "completed"
    assert settled.children[0].result_status == "local-only"
