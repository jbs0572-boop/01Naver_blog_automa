from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import threading
from multiprocessing.connection import Connection
from pathlib import Path
from typing import final, override

import pytest

from tests.test_runner_prd import (
    DATE_CONTEXT,
    NOW,
    FixtureExecutor,
    FixtureNaver,
    FixtureNotion,
)
from tools.contract_types import JSONMap
from tools.runner_execution import confirm_job, recover_job, resume_job, run_job
from tools.runner_lock import acquire_lock
from tools.runner_secure_fs import runner_secure_storage
from tools.runner_state import state_paths
from tools.runner_types import ConfirmationInput, RunnerRequest, RunnerResult, RunStatus


@final
class SlowSaveNaver(FixtureNaver):
    def __init__(self) -> None:
        self.save_started: threading.Event = threading.Event()
        self.release_save: threading.Event = threading.Event()

    @override
    def save(self, title: str, artifact_digest: str) -> JSONMap:
        self.save_started.set()
        assert self.release_save.wait(timeout=5)
        return super().save(title, artifact_digest)


def _install_rollout(root: Path) -> None:
    config = root / "config"
    config.mkdir()
    _ = shutil.copy(
        Path(__file__).resolve().parents[1] / "config/topic-feedback-rollout.json",
        config,
    )


def _hold_run_lease(
    root_text: str,
    run_id: str,
    ready: Connection,
    release: Connection,
) -> None:
    root = Path(root_text)
    _, _, lock_path = state_paths(root, run_id)
    with (
        ready,
        release,
        runner_secure_storage((root, root / ".automation")),
        acquire_lock(lock_path, {"pid": os.getpid(), "run_id": run_id}),
    ):
        ready.send_bytes(b"ready")
        _ = release.recv_bytes()


def test_live_confirmation_lease_prevents_recovery_drift_mutation(
    tmp_path: Path,
) -> None:
    # Given: confirmation is paused inside Naver save while holding the run lease.
    _install_rollout(tmp_path)
    _ = (tmp_path / "notion-config.md").write_text(
        "- 데이터 소스 ID: `datasource-fixture`\n", encoding="utf-8"
    )
    naver = SlowSaveNaver()
    waiting = run_job(
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
    if waiting.status is RunStatus.READY_FOR_NAVER:
        waiting = resume_job(
            RunnerRequest(
                root=tmp_path,
                job="",
                run_id=waiting.run_id,
                resume=True,
                executor=FixtureExecutor(),
                notion_adapter=FixtureNotion(),
                naver_adapter=naver,
            )
        )
    saved: list[RunnerResult] = []
    waiting_state = json.loads(waiting.state_path.read_text(encoding="utf-8"))
    confirmation_nonce = str(waiting_state["confirmation_nonce"])

    def confirm() -> None:
        saved.append(
            confirm_job(
                ConfirmationInput(
                    tmp_path,
                    waiting.run_id,
                    "naver-draft-save",
                    executor=FixtureExecutor(),
                    notion_adapter=FixtureNotion(),
                    naver_adapter=naver,
                    confirmation_nonce=confirmation_nonce,
                )
            )
        )

    confirmation = threading.Thread(target=confirm)
    confirmation.start()
    assert naver.save_started.wait(timeout=5)
    research = tmp_path / "research/fixture.md"
    _ = research.write_text("# changed while save is live\n", encoding="utf-8")
    state_before = waiting.state_path.read_bytes()
    log_before = waiting.log_path.read_bytes()

    # When: recovery observes input drift while the live owner still holds the lease.
    try:
        recovery = recover_job(
            RunnerRequest(tmp_path, "", run_id=waiting.run_id, now=NOW)
        )
        state_during = waiting.state_path.read_bytes()
        log_during = waiting.log_path.read_bytes()
    finally:
        naver.release_save.set()
        confirmation.join(timeout=5)

    # Then: recovery reports a conflict without mutation and terminal state stays final.
    assert not confirmation.is_alive()
    assert recovery.status is RunStatus.BLOCKED
    assert recovery.message.startswith("runner execution is already locked:")
    assert state_during == state_before
    assert log_during == log_before
    assert saved[0].status is RunStatus.DRAFT_SAVED
    final_state = waiting.state_path.read_bytes()
    final_log = waiting.log_path.read_bytes()
    later = recover_job(RunnerRequest(tmp_path, "", run_id=waiting.run_id, now=NOW))
    assert later.status is RunStatus.DRAFT_SAVED
    assert waiting.state_path.read_bytes() == final_state
    assert waiting.log_path.read_bytes() == final_log


def test_idle_input_drift_still_persists_blocked_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an idle recoverable state whose governed input fingerprint changed.
    run_id = "RUN-idle-drift"
    state_path = tmp_path / ".automation/state" / f"{run_id}.json"
    state_path.parent.mkdir(parents=True)
    state: JSONMap = {"input_hash": "old", "status": RunStatus.RUNNING.value}
    _ = state_path.write_text(json.dumps(state), encoding="utf-8")
    recovered = RunnerRequest(tmp_path, "weekly-improve", run_id=run_id)

    def recovered_request(
        _state: JSONMap, _root: Path, _state_dir: Path | None
    ) -> RunnerRequest:
        return recovered

    def changed_fingerprint(
        _request: RunnerRequest, **_kwargs: object
    ) -> str:
        return "new"

    monkeypatch.setattr("tools.runner_execution.request_from_state", recovered_request)
    monkeypatch.setattr("tools.runner_execution.input_fingerprint", changed_fingerprint)

    # When: recovery runs without a competing owner.
    result = recover_job(RunnerRequest(tmp_path, "", run_id=run_id, now=NOW))

    # Then: the historical drift contract remains a persisted blocked state.
    assert result.status is RunStatus.BLOCKED
    assert json.loads(state_path.read_text())["status"] == RunStatus.BLOCKED.value
    assert not (tmp_path / ".automation/locks" / f"{run_id}.lock").exists()


def test_stale_recovery_lock_is_cleaned_before_leased_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a dead owner's lock guards an otherwise valid recoverable state.
    run_id = "RUN-stale-recovery"
    state_path = tmp_path / ".automation/state" / f"{run_id}.json"
    lock_path = tmp_path / ".automation/locks" / f"{run_id}.lock"
    state_path.parent.mkdir(parents=True)
    lock_path.parent.mkdir(parents=True)
    _ = state_path.write_text(
        json.dumps({"input_hash": "same", "status": RunStatus.RUNNING.value}),
        encoding="utf-8",
    )
    _ = lock_path.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    recovered = RunnerRequest(tmp_path, "weekly-improve", run_id=run_id)

    def recovered_request(
        _state: JSONMap, _root: Path, _state_dir: Path | None
    ) -> RunnerRequest:
        return recovered

    def unchanged_fingerprint(
        _request: RunnerRequest, **_kwargs: object
    ) -> str:
        return "same"

    monkeypatch.setattr("tools.runner_execution.request_from_state", recovered_request)
    monkeypatch.setattr(
        "tools.runner_execution.input_fingerprint", unchanged_fingerprint
    )

    def execute_under_lease(
        _request: RunnerRequest,
        allow_existing: bool = False,
        *,
        lease_held: bool = False,
    ) -> RunnerResult:
        assert allow_existing and lease_held and lock_path.exists()
        return RunnerResult(
            run_id, RunStatus.PASSED, state_path, state_path, (), "passed"
        )

    monkeypatch.setattr("tools.runner_execution._run", execute_under_lease)

    # When: recovery takes over the stale lease.
    result = recover_job(RunnerRequest(tmp_path, "", run_id=run_id, now=NOW))

    # Then: execution occurs under the replacement lease and cleanup removes it.
    assert result.status is RunStatus.PASSED
    assert not lock_path.exists()


def test_process_owner_blocks_recovery_before_state_read(tmp_path: Path) -> None:
    # Given: another process owns the run lease around unchanged state bytes.
    run_id = "RUN-process-owner"
    state_path, log_path, _ = state_paths(tmp_path, run_id)
    state_path.parent.mkdir(parents=True)
    _ = state_path.write_bytes(b'{"sentinel":"unchanged"}\n')
    ready_receive, ready_send = multiprocessing.Pipe(duplex=False)
    release_receive, release_send = multiprocessing.Pipe(duplex=False)
    owner = multiprocessing.Process(
        target=_hold_run_lease,
        args=(str(tmp_path), run_id, ready_send, release_receive),
    )
    owner.start()
    assert ready_receive.poll(5)
    _ = ready_receive.recv_bytes()

    # When: recovery enters while that process still owns the lease.
    try:
        result = recover_job(RunnerRequest(tmp_path, "", run_id=run_id, now=NOW))
    finally:
        release_send.send_bytes(b"release")
        owner.join(timeout=5)
        ready_receive.close()
        release_send.close()

    # Then: it reports conflict without reading or mutating state/log storage.
    assert not owner.is_alive()
    assert owner.exitcode == 0
    assert result.status is RunStatus.BLOCKED
    assert state_path.read_bytes() == b'{"sentinel":"unchanged"}\n'
    assert not log_path.exists()
