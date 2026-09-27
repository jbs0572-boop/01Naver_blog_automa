from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Literal, cast

import pytest

from tests.test_dashboard_manual_store import persist_legacy_pending_batch
from tools.contract_types import ContractError, JSONMap
from tools.dashboard_manual_actions import execute_child_action
from tools.dashboard_manual_batch import new_batch
from tools.dashboard_manual_models import (
    ConfirmationPreview,
    ManualActionView,
    ManualActiveActionView,
    ManualBatchView,
    ManualRunContext,
    ManualRunDependencies,
    ManualRunView,
)
from tools.dashboard_manual_request import parse_manual_run_payload
from tools.dashboard_manual_run import ManualRunManager
from tools.dashboard_manual_store import ManualBatchStore
from tools.naver_adapter import NaverBrowserAdapter
from tools.runner_state import state_paths
from tools.runner_types import ConfirmationInput, RunnerRequest, RunnerResult, RunStatus


def _fixture_runner(request: RunnerRequest) -> RunnerResult:
    return RunnerResult(
        request.run_id or "RUN-fixture",
        RunStatus.LOCAL_ONLY,
        request.root / "state.json",
        request.root / "run.jsonl",
        (),
        "fixture completed",
    )


def _pending_current_batch(tmp_path: Path) -> ManualBatchView:
    batch = new_batch(
        parse_manual_run_payload(
            {"auto_topic": True, "as_of_date": "2026-09-07"}
        ),
        tmp_path,
    )
    ManualBatchStore(tmp_path).save(batch)
    return batch


def _settled_current_demo(tmp_path: Path) -> tuple[ManualRunManager, str, str, str]:
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(_fixture_runner)
    )
    batch = manager.start(
        parse_manual_run_payload(
            {"auto_topic": True, "as_of_date": "2026-09-07"}
        )
    )
    manager.close()
    settled = manager.get(batch.batch_id)
    assert settled is not None
    child = settled.children[0]
    assert child.child_id is not None and child.next_action is not None
    return manager, batch.batch_id, child.child_id, child.next_action.nonce


def _settled_three_child_demo(tmp_path: Path) -> tuple[ManualRunManager, str, str, str]:
    batch = persist_legacy_pending_batch(tmp_path)
    settled_children = tuple(
        replace(
            child,
            status="completed",
            result_status=RunStatus.LOCAL_ONLY.value,
            message="fixture completed",
            keyword=f"과거 주제 {slot}",
            resolved_keyword=f"과거 주제 {slot}",
            next_action=ManualActionView("external", f"nonce-{slot}"),
            ended_at=child.updated_at,
        )
        for slot, child in enumerate(batch.children, start=1)
    )
    settled_batch = replace(batch, status="completed", children=settled_children)
    ManualBatchStore(tmp_path).save(settled_batch)
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(_fixture_runner)
    )
    manager.close()
    settled = manager.get(batch.batch_id)
    assert settled is not None
    child = settled.children[0]
    assert child.child_id is not None and child.next_action is not None
    return manager, batch.batch_id, child.child_id, child.next_action.nonce


def test_confirm_action_forwards_active_dashboard_nonce_to_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a dashboard child with an active confirmation action nonce.
    nonce = "active-confirmation-nonce"
    child = ManualRunView(
        "TASK-1",
        "queued",
        "2026-09-13T00:00:00+09:00",
        "2026-09-13T00:00:00+09:00",
        run_id="RUN-1",
        confirmation_preview=ConfirmationPreview(
            "naver-draft-save", "blog", "title", ("assets/body.png",), "sha256:x"
        ),
        next_action=ManualActionView("confirm", nonce),
    )
    captured: list[ConfirmationInput] = []

    def confirm(confirmation: ConfirmationInput) -> RunnerResult:
        captured.append(confirmation)
        return RunnerResult(
            "RUN-1", RunStatus.LOCAL_ONLY, tmp_path / "state.json", tmp_path / "run.jsonl", (), "done"
        )

    monkeypatch.setattr("tools.dashboard_manual_actions.confirm_job", confirm)

    # When: the dashboard executes the confirmation action.
    _ = execute_child_action(
        ManualRunContext(tmp_path, True),
        ManualRunDependencies(_fixture_runner),
        child,
        "confirm",
    )

    # Then: the runner receives the exact nonce that the dashboard validated.
    assert captured[0].confirmation_nonce == nonce


def test_two_threads_submit_same_nonce_exactly_once(tmp_path: Path) -> None:
    manager, batch_id, child_id, nonce = _settled_current_demo(tmp_path)
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(_fixture_runner)
    )
    barrier = threading.Barrier(2)
    accepted: list[bool] = []
    rejected: list[ContractError] = []

    def submit() -> None:
        _ = barrier.wait()
        try:
            _ = manager.submit_action(batch_id, child_id, "external", nonce)
            accepted.append(True)
        except ContractError as error:
            rejected.append(error)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    manager.close()

    assert len(accepted) == 1
    assert len(rejected) == 1


def test_stale_nonce_is_rejected_after_restart(tmp_path: Path) -> None:
    _manager, batch_id, child_id, nonce = _settled_current_demo(tmp_path)
    restarted = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(_fixture_runner)
    )
    _ = restarted.submit_action(batch_id, child_id, "external", nonce)
    restarted.close()
    second = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(_fixture_runner)
    )

    with pytest.raises(ContractError):
        _ = second.submit_action(batch_id, child_id, "external", nonce)
    second.close()


def test_q1_retry_action_is_rejected_after_cumulative_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    manager, batch_id, child_id, nonce = _settled_current_demo(tmp_path)
    before = manager.get(batch_id)
    assert before is not None
    child = before.children[0]
    assert child.run_id is not None
    exhausted_state = {
        "run_id": child.run_id,
        "status": RunStatus.FAILED.value,
        "stages": {"content-assembler": RunStatus.FAILED.value},
        "stage_attempts": {"content-assembler": 3},
    }
    state_path, _, _ = state_paths(tmp_path, child.run_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _ = state_path.write_text(json.dumps(exhausted_state), encoding="utf-8")
    failed = replace(
        child,
        status="failed",
        result_status=RunStatus.FAILED.value,
        next_action=ManualActionView("retry", nonce),
        retryable=True,
    )
    ManualBatchStore(tmp_path).save(
        replace(before, children=(failed, *before.children[1:]))
    )

    with pytest.raises(ContractError, match="Q1 retry limit exhausted"):
        _ = manager.submit_action(batch_id, child_id, "retry", nonce)
    manager.close()


def test_external_action_targets_only_one_child(tmp_path: Path) -> None:
    manager, batch_id, child_id, nonce = _settled_current_demo(tmp_path)
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(_fixture_runner)
    )
    before = manager.get(batch_id)
    assert before is not None
    _ = manager.submit_action(batch_id, child_id, "external", nonce)
    manager.close()
    after = manager.get(batch_id)

    assert after is not None
    assert len(after.children) == 1
    assert after.children[0].message == "외부 저장 대기 · Notion/Naver 연결이 필요합니다"


def test_retired_three_child_actions_remain_visible_but_cannot_execute(
    tmp_path: Path,
) -> None:
    manager, batch_id, child_id, nonce = _settled_three_child_demo(tmp_path)
    batch = manager.get(batch_id)
    assert batch is not None
    batch_path = ManualBatchStore(tmp_path).store_path(batch_id)
    original = batch_path.read_bytes()
    calls: list[RunnerRequest] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        calls.append(request)
        return _fixture_runner(request)

    restarted = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(runner)
    )

    with pytest.raises(ContractError, match="retired manual batch is read-only"):
        _ = restarted.submit_action(batch_id, child_id, "external", nonce)
    restarted.close()

    reloaded = restarted.get(batch_id)
    assert reloaded is not None
    assert [child.resolved_keyword for child in reloaded.children] == [
        "과거 주제 1",
        "과거 주제 2",
        "과거 주제 3",
    ]
    assert all(child.next_action is not None for child in reloaded.children)
    assert calls == []
    assert batch_path.read_bytes() == original


def test_accepted_child_action_marks_batch_running_until_scoped_work_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _settled_manager, batch_id, child_id, nonce = _settled_current_demo(tmp_path)
    persisted = ManualBatchStore(tmp_path).get(batch_id)
    assert persisted is not None
    ManualBatchStore(tmp_path).save(replace(persisted, children=(persisted.children[0],)))
    manager = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(_fixture_runner)
    )
    before = manager.get(batch_id)
    assert before is not None
    started = Event()
    release = Event()

    def blocked_action(
        _context: ManualRunContext,
        _dependencies: ManualRunDependencies,
        child: ManualRunView,
        _kind: str,
    ) -> ManualRunView:
        started.set()
        assert release.wait(timeout=1)
        return replace(
            child,
            status="completed",
            message="외부 저장 대기 · Notion/Naver 연결이 필요합니다",
            next_action=ManualActionView("external", "fresh-nonce"),
        )

    monkeypatch.setattr("tools.dashboard_manual_run.execute_child_action", blocked_action)

    # When
    accepted = manager.submit_action(batch_id, child_id, "external", nonce)

    # Then
    assert accepted.status == "running"
    assert accepted.children[0].active_action is not None
    assert started.wait(timeout=1)
    release.set()
    manager.close()
    settled = manager.get(batch_id)
    assert settled is not None
    assert settled.status == "completed"
    assert settled.children[0].next_action == ManualActionView("external", "fresh-nonce")


def test_restart_requeues_interrupted_initial_without_new_child_or_snapshot(
    tmp_path: Path,
) -> None:
    manager, batch_id, _child_id, _nonce = _settled_current_demo(tmp_path)
    before = manager.get(batch_id)
    assert before is not None
    identities = [(child.child_id, child.run_id) for child in before.children]
    capture_id = before.snapshot.capture_id if before.snapshot else None

    restarted = ManualRunManager(
        ManualRunContext(tmp_path, False), ManualRunDependencies(_fixture_runner)
    )
    restarted.close()
    after = restarted.get(batch_id)

    assert after is not None
    assert [(child.child_id, child.run_id) for child in after.children] == identities
    assert (after.snapshot.capture_id if after.snapshot else None) == capture_id


def test_restart_requires_fresh_naver_preparation_before_confirmation(
    tmp_path: Path,
) -> None:
    manager, batch_id, _child_id, _nonce = _settled_current_demo(tmp_path)
    before = manager.get(batch_id)
    assert before is not None
    prepared = replace(
        before.children[0],
        result_status=RunStatus.AWAITING_USER_CONFIRMATION.value,
        message="awaiting_user_confirmation",
        confirmation_preview=None,
        next_action=ManualActionView("confirm", "stale-confirmation"),
    )
    ManualBatchStore(tmp_path).save(replace(before, children=(prepared, *before.children[1:])))
    state_path = tmp_path / ".automation" / "state" / f"{prepared.run_id}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _ = state_path.write_text(
        json.dumps(
            {
                "status": RunStatus.AWAITING_USER_CONFIRMATION.value,
                "stages": {"naver-rider": RunStatus.PASSED.value},
                "stage_execution": {"naver-rider": "produced"},
                "target_blog_id": "sola_note",
                "naver_tab_target_id": "stale-tab",
            }
        ),
        encoding="utf-8",
    )

    restarted = ManualRunManager(
        ManualRunContext(tmp_path, True),
        ManualRunDependencies(
            _fixture_runner,
            naver_adapter=cast("NaverBrowserAdapter", object()),
        ),
    )
    restarted.close()
    after = restarted.get(batch_id)

    assert after is not None
    recovered = after.children[0]
    assert recovered.result_status == RunStatus.LOCAL_ONLY.value
    assert recovered.next_action is not None
    assert recovered.next_action.kind == "external"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["stages"]["naver-rider"] == RunStatus.PENDING.value
    assert "naver_tab_target_id" not in persisted


def test_retry_after_failed_confirmed_save_requires_fresh_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a confirmed Naver save failed after Q1 and Q2 have already passed.
    manager, batch_id, _child_id, _nonce = _settled_current_demo(tmp_path)
    before = manager.get(batch_id)
    assert before is not None
    child = replace(
        before.children[0],
        result_status=RunStatus.FAILED.value,
        message="Naver save failed",
        retryable=True,
        confirmation_preview=None,
        next_action=ManualActionView("retry", "retry-after-confirmed-save"),
    )
    ManualBatchStore(tmp_path).save(replace(before, children=(child, *before.children[1:])))

    state_path = tmp_path / ".automation" / "state" / f"{child.run_id}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "status": RunStatus.FAILED.value,
        "message": "Naver save failed",
        "confirmation": {"action": "naver-draft-save"},
        "stages": {
            "content-assembler": RunStatus.PASSED.value,
            "notion-rider": RunStatus.PASSED.value,
            "naver-rider": RunStatus.FAILED.value,
        },
        "stage_execution": {
            "content-assembler": "produced",
            "notion-rider": "produced",
            "naver-rider": "attempted",
        },
        "notion_page_id": "page-q2",
        "notion_roundtrip_digest": "sha256:" + "q" * 64,
        "target_blog_id": "sola_note",
        "naver_tab_target_id": "prepared-tab",
    }
    _ = state_path.write_text(json.dumps(state), encoding="utf-8")
    saves: list[str] = []

    def unsafe_recoverer(request: RunnerRequest) -> RunnerResult:
        persisted = json.loads(
            (tmp_path / ".automation" / "state" / f"{request.run_id}.json").read_text(
                encoding="utf-8"
            )
        )
        if persisted.get("confirmation") is not None:
            saves.append("saved-from-stale-confirmation")
        raise AssertionError("retry must renew Naver preparation before recovery")

    # When: the operator retries the failed confirmed save.
    retry = child.next_action
    assert child.child_id is not None and retry is not None
    retried = ManualRunManager(
        ManualRunContext(tmp_path, True),
        ManualRunDependencies(_fixture_runner, recoverer=unsafe_recoverer),
    )
    _ = retried.submit_action(batch_id, child.child_id, "retry", retry.nonce)
    retried.close()

    # Then: the stale confirmation cannot reach save; Q1/Q2 identity survives and
    # the only next action is a fresh external preparation.
    after = retried.get(batch_id)
    assert after is not None
    recovered = after.children[0]
    assert saves == []
    assert recovered.status == "completed"
    assert recovered.result_status == RunStatus.LOCAL_ONLY.value
    assert recovered.confirmation_preview is None
    assert recovered.next_action is not None
    assert recovered.next_action.kind == "external"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["status"] == RunStatus.LOCAL_ONLY.value
    assert persisted["stages"]["content-assembler"] == RunStatus.PASSED.value
    assert persisted["stages"]["notion-rider"] == RunStatus.PASSED.value
    assert persisted["notion_page_id"] == "page-q2"
    assert persisted["notion_roundtrip_digest"] == "sha256:" + "q" * 64
    assert "confirmation" not in persisted
    assert "target_blog_id" not in persisted
    assert "naver_tab_target_id" not in persisted

    class RecordingNaver:
        def __init__(self) -> None:
            self.prepared_titles: list[str] = []
            self.saved_titles: list[str] = []

        @property
        def target_blog_id(self) -> str:
            return "sola_note"

        def prepare(self, title: str, body: str, artifact_digest: str) -> JSONMap:
            _ = (body, artifact_digest)
            self.prepared_titles.append(title)
            return {"target_blog_id": "sola_note", "naver_title": title}

        def save(self, title: str, artifact_digest: str) -> JSONMap:
            _ = artifact_digest
            self.saved_titles.append(title)
            return {"draft_status": "saved", "naver_title": title}

    naver = RecordingNaver()

    def resume(request: RunnerRequest) -> RunnerResult:
        assert request.run_id == child.run_id
        assert request.naver_adapter is naver
        external_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert external_state["status"] == RunStatus.LOCAL_ONLY.value
        assert "confirmation" not in external_state
        _ = naver.prepare("새 준비 제목", "새 준비 본문", "sha256:" + "a" * 64)
        external_state.update(
            {
                "status": RunStatus.AWAITING_USER_CONFIRMATION.value,
                "message": "awaiting_user_confirmation",
                "target_blog_id": "sola_note",
                "naver_title": "새 준비 제목",
                "artifact_digest": "sha256:" + "a" * 64,
                "artifact_paths": ["assets/retry/body.png"],
                "confirmation_requested_at": "2026-09-08T00:00:00+00:00",
                "confirmation_nonce": "new-confirmation-nonce",
            }
        )
        external_state["stages"]["naver-rider"] = RunStatus.PASSED.value
        external_state["stage_execution"]["naver-rider"] = "produced"
        _ = state_path.write_text(json.dumps(external_state), encoding="utf-8")
        return RunnerResult(
            str(request.run_id),
            RunStatus.AWAITING_USER_CONFIRMATION,
            state_path,
            tmp_path / ".automation" / "logs" / f"{request.run_id}.jsonl",
            (),
            "awaiting_user_confirmation",
        )

    def confirm(confirmation: ConfirmationInput) -> RunnerResult:
        assert confirmation.run_id == child.run_id
        assert confirmation.action == "naver-draft-save"
        confirmation_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert confirmation_state["status"] == RunStatus.AWAITING_USER_CONFIRMATION.value
        _ = naver.save(
            str(confirmation_state["naver_title"]),
            str(confirmation_state["artifact_digest"]),
        )
        confirmation_state["confirmation"] = {"action": confirmation.action}
        confirmation_state["status"] = RunStatus.DRAFT_SAVED.value
        _ = state_path.write_text(json.dumps(confirmation_state), encoding="utf-8")
        return RunnerResult(
            confirmation.run_id,
            RunStatus.DRAFT_SAVED,
            state_path,
            tmp_path / ".automation" / "logs" / f"{confirmation.run_id}.jsonl",
            (),
            "draft_saved",
        )

    monkeypatch.setattr("tools.dashboard_manual_actions.resume_job", resume)
    monkeypatch.setattr("tools.dashboard_manual_actions.confirm_job", confirm)
    external = ManualRunManager(
        ManualRunContext(tmp_path, True),
        ManualRunDependencies(_fixture_runner, naver_adapter=naver),
    )

    # When: external preparation resumes, then the operator confirms its new nonce.
    prepared = external.continue_external(recovered.task_id)
    assert prepared.result_status == RunStatus.AWAITING_USER_CONFIRMATION.value
    assert prepared.next_action is not None
    assert prepared.next_action.kind == "confirm"
    assert prepared.next_action.nonce != retry.nonce
    assert naver.prepared_titles == ["새 준비 제목"]
    assert naver.saved_titles == []
    saved = external.confirm(recovered.task_id)

    # Then: only the new confirmation can issue the draft save.
    assert saved.result_status == RunStatus.DRAFT_SAVED.value
    assert naver.saved_titles == ["새 준비 제목"]


@pytest.mark.parametrize(
    ("persisted_status", "persisted_result_status"),
    (("completed", RunStatus.RUNNING.value), ("running", None)),
)
def test_restart_recovers_child_with_interrupted_running_state(
    tmp_path: Path,
    persisted_status: Literal["completed", "running"],
    persisted_result_status: str | None,
) -> None:
    initial, batch_id, _child_id, _nonce = _settled_current_demo(tmp_path)
    before = initial.get(batch_id)
    assert before is not None
    child = replace(
        before.children[0],
        status=persisted_status,
        result_status=persisted_result_status,
        resolved_keyword=None,
        keyword=None,
        next_action=None,
    )
    ManualBatchStore(tmp_path).save(
        replace(before, children=(child,))
    )
    calls: list[RunnerRequest] = []

    def recoverer(request: RunnerRequest) -> RunnerResult:
        calls.append(request)
        assert request.run_id is not None
        state_path = tmp_path / ".automation" / "state" / f"{request.run_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _ = state_path.write_text(
            json.dumps(
                {
                    "run_id": request.run_id,
                    "status": "local-only",
                    "message": "recovered",
                    "keyword": "셋째 주제",
                    "selection_snapshot_sha256": "sha256:" + "d" * 64,
                }
            ),
            encoding="utf-8",
        )
        return RunnerResult(
            request.run_id,
            RunStatus.LOCAL_ONLY,
            state_path,
            tmp_path / ".automation" / "logs" / f"{request.run_id}.jsonl",
            (),
            "recovered",
        )

    restarted = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(_fixture_runner, recoverer=recoverer),
    )
    restarted.close()
    after = restarted.get(batch_id)

    assert after is not None
    assert len(calls) == 1
    assert after.children[0].result_status == RunStatus.LOCAL_ONLY.value
    assert after.children[0].resolved_keyword == "셋째 주제"
    assert after.children[0].next_action is not None
    assert after.children[0].next_action.kind == "external"


def test_retry_blocked_selector_reuses_run_and_advances_remaining_slots(
    tmp_path: Path,
) -> None:
    batch = _pending_current_batch(tmp_path)
    calls: list[RunnerRequest] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        calls.append(request)
        slot = request.selection_context.batch_slot if request.selection_context else 1
        assert slot is not None and request.run_id is not None
        state_path = tmp_path / ".automation" / "state" / f"{request.run_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        first_failure = slot == 1 and len(calls) == 1
        state = {
            "run_id": request.run_id,
            "status": "failed" if first_failure else "local-only",
            "message": "selector failed" if first_failure else "local-only",
            "stages": {"topic-selector": "failed" if first_failure else "passed"},
        }
        if not first_failure:
            state.update(
                {
                    "keyword": ("재시도 주제", "둘째 주제", "셋째 주제")[slot - 1],
                    "selection_snapshot_sha256": "sha256:" + "b" * 64,
                }
            )
        _ = state_path.write_text(json.dumps(state), encoding="utf-8")
        return RunnerResult(
            request.run_id,
            RunStatus.FAILED if first_failure else RunStatus.LOCAL_ONLY,
            state_path,
            tmp_path / ".automation" / "logs" / f"{request.run_id}.jsonl",
            (),
            str(state["message"]),
        )

    manager = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(runner, recoverer=runner),
    )
    manager.close()
    blocked = manager.get(batch.batch_id)
    assert blocked is not None
    child = blocked.children[0]
    assert child.child_id is not None and child.next_action is not None
    original_run_id = child.run_id

    restarted = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(runner, recoverer=runner),
    )
    _ = restarted.submit_action(
        batch.batch_id, child.child_id, "retry", child.next_action.nonce
    )
    restarted.close()
    settled = restarted.get(batch.batch_id)

    assert settled is not None
    assert settled.children[0].run_id == original_run_id
    assert settled.snapshot is not None
    assert settled.snapshot.sha256 == "sha256:" + "b" * 64
    assert [item.resolved_keyword for item in settled.children] == ["재시도 주제"]


def test_restart_recovers_accepted_retry_through_initial_settlement(
    tmp_path: Path,
) -> None:
    batch = _pending_current_batch(tmp_path)
    calls: list[RunnerRequest] = []

    def runner(request: RunnerRequest) -> RunnerResult:
        calls.append(request)
        context = request.selection_context
        assert context is not None
        assert request.run_id is not None
        slot = context.batch_slot
        assert slot is not None
        failed = slot == 1 and len(calls) == 1
        state_path = tmp_path / ".automation" / "state" / f"{request.run_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state: dict[str, str | dict[str, str]] = {
            "run_id": request.run_id,
            "status": "failed" if failed else "local-only",
            "message": "selector failed" if failed else "local-only",
            "stages": {"topic-selector": "failed" if failed else "passed"},
        }
        if not failed:
            state["keyword"] = ("재시도 주제", "둘째 주제", "셋째 주제")[slot - 1]
            state["selection_snapshot_sha256"] = "sha256:" + "c" * 64
        _ = state_path.write_text(json.dumps(state), encoding="utf-8")
        return RunnerResult(
            request.run_id,
            RunStatus.FAILED if failed else RunStatus.LOCAL_ONLY,
            state_path,
            tmp_path / ".automation" / "logs" / f"{request.run_id}.jsonl",
            (),
            str(state["message"]),
        )

    initial = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(runner, recoverer=runner),
    )
    initial.close()
    blocked = initial.get(batch.batch_id)
    assert blocked is not None
    failed_child = blocked.children[0]
    assert failed_child.child_id is not None and failed_child.next_action is not None
    original_run_id = failed_child.run_id

    accepted_child = replace(
        failed_child,
        next_action=None,
        status="queued",
        active_action=ManualActiveActionView(
            "retry",
            "OP-persisted-retry",
            "sha256:" + hashlib.sha256(failed_child.next_action.nonce.encode()).hexdigest(),
            "2026-09-07T00:00:00+00:00",
            "queued",
        ),
    )
    ManualBatchStore(tmp_path).save(replace(blocked, children=(accepted_child, *blocked.children[1:])))

    recovered = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(runner, recoverer=runner),
    )
    recovered.close()

    settled = recovered.get(batch.batch_id)
    assert settled is not None
    assert settled.children[0].run_id == original_run_id
    assert settled.snapshot is not None
    assert settled.snapshot.sha256 == "sha256:" + "c" * 64
    assert [item.resolved_keyword for item in settled.children] == ["재시도 주제"]
    assert [
        call.selection_context.batch_slot
        for call in calls
        if call.selection_context is not None
    ] == [1, 1]


def test_restart_recovers_chained_persisted_children_without_initial_runner(
    tmp_path: Path,
) -> None:
    initial, batch_id, _child_id, _nonce = _settled_three_child_demo(tmp_path)
    before = initial.get(batch_id)
    assert before is not None
    recovered_children = tuple(
        replace(
            child,
            status="queued" if child.slot in {1, 2} else "running",
            result_status=RunStatus.RUNNING.value if child.slot == 3 else None,
            keyword=None,
            resolved_keyword=None,
            next_action=None,
            active_action=(
                ManualActiveActionView(
                    "retry",
                    f"OP-retry-{child.slot}",
                    "sha256:" + "e" * 64,
                    "2026-09-07T00:00:00+00:00",
                    "queued",
                )
                if child.slot in {1, 2}
                else None
            ),
        )
        for child in before.children
    )
    ManualBatchStore(tmp_path).save(replace(before, children=recovered_children))
    calls: list[RunnerRequest] = []

    def recoverer(request: RunnerRequest) -> RunnerResult:
        calls.append(request)
        assert request.run_id is not None
        context = request.selection_context
        assert context is not None and context.batch_slot is not None
        state_path = tmp_path / ".automation" / "state" / f"{request.run_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _ = state_path.write_text(
            json.dumps(
                {
                    "run_id": request.run_id,
                    "status": "local-only",
                    "message": "recovered",
                    "keyword": ("첫째 주제", "둘째 주제", "셋째 주제")[
                        context.batch_slot - 1
                    ],
                    "selection_snapshot_sha256": "sha256:" + "e" * 64,
                }
            ),
            encoding="utf-8",
        )
        return RunnerResult(
            request.run_id,
            RunStatus.LOCAL_ONLY,
            state_path,
            tmp_path / ".automation" / "logs" / f"{request.run_id}.jsonl",
            (),
            "recovered",
        )

    restarted = ManualRunManager(
        ManualRunContext(tmp_path, False),
        ManualRunDependencies(_fixture_runner, recoverer=recoverer),
    )
    restarted.close()
    after = restarted.get(batch_id)

    assert after is not None
    assert [
        request.selection_context.batch_slot
        for request in calls
        if request.selection_context is not None
    ] == []
    assert [child.resolved_keyword for child in after.children] == [None, None, None]
