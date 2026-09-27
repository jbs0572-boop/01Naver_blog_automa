from __future__ import annotations

import json
import multiprocessing
import os
import threading
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.runner_confirmation import apply_confirmation
from tools.runner_secure_fs import runner_secure_storage, secure_append
from tools.runner_state import read_state as read_runner_state
from tools.runner_state import state_paths
from tools.runner_types import (
    ConfirmationInput,
    RunnerBlocked,
    RunnerRequest,
    RunnerResult,
    RunStatus,
)


def _fd_count() -> int:
    return len(os.listdir("/dev/fd"))


def _append_in_process(root_text: str, log_text: str, writer: int) -> None:
    root = Path(root_text)
    log_path = Path(log_text)
    with runner_secure_storage((root,)):
        for sequence in range(20):
            encoded = (
                json.dumps({"sequence": sequence, "writer": writer}).encode() + b"\n"
            )
            secure_append(log_path, encoded)


def test_failed_partial_append_cannot_erase_concurrent_complete_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: writer A pauses after one byte while writer B targets the same log.
    log_path = tmp_path / "logs" / "RUN-race.jsonl"
    log_path.parent.mkdir()
    seed = b'{"seed":true}\n'
    event_b = b'{"writer":"b"}\n'
    _ = log_path.write_bytes(seed)
    writer_a_started = threading.Event()
    writer_b_started = threading.Event()
    writer_b_completed = threading.Event()
    original_write = os.write
    first_a_write = True
    outcomes: dict[str, BaseException | None] = {}
    descriptors_before = _fd_count()

    def controlled_write(descriptor: int, encoded: bytes) -> int:
        nonlocal first_a_write
        if threading.current_thread().name == "writer-a" and first_a_write:
            first_a_write = False
            _ = original_write(descriptor, encoded[:1])
            writer_a_started.set()
            assert writer_b_started.wait(timeout=2)
            _ = writer_b_completed.wait(timeout=0.2)
            raise OSError("injected writer A failure")
        return original_write(descriptor, encoded)

    monkeypatch.setattr("tools.runner_secure_fs_core.os.write", controlled_write)

    def write_a() -> None:
        try:
            with runner_secure_storage((tmp_path,)):
                secure_append(log_path, b'{"writer":"a"}\n')
        except ContractError as error:
            outcomes["a"] = error

    def write_b() -> None:
        writer_b_started.set()
        try:
            with runner_secure_storage((tmp_path,)):
                secure_append(log_path, event_b)
        except ContractError as error:
            outcomes["b"] = error
        else:
            outcomes["b"] = None
            writer_b_completed.set()

    # When: B appends while A is paused, then A fails and rolls back.
    thread_a = threading.Thread(target=write_a, name="writer-a")
    thread_a.start()
    assert writer_a_started.wait(timeout=2)
    thread_b = threading.Thread(target=write_b, name="writer-b")
    thread_b.start()
    thread_a.join(timeout=3)
    thread_b.join(timeout=3)

    # Then: B's complete JSONL event survives and no descriptor leaks.
    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert isinstance(outcomes.get("a"), ContractError)
    assert outcomes.get("b") is None
    assert log_path.read_bytes() == seed + event_b
    assert all(json.loads(line) for line in log_path.read_text().splitlines())
    assert _fd_count() == descriptors_before


def test_append_fails_closed_when_leaf_is_replaced_after_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an adversary is ready to replace the log after the writer writes.
    log_path = tmp_path / "logs" / "RUN-replaced.jsonl"
    replacement = log_path.with_suffix(".replacement")
    log_path.parent.mkdir()
    _ = log_path.write_bytes(b'{"seed":true}\n')
    replacement_bytes = b'{"replacement":true}\n'
    _ = replacement.write_bytes(replacement_bytes)
    original_write = os.write
    replaced = False

    def replace_after_write(descriptor: int, encoded: bytes) -> int:
        nonlocal replaced
        written = original_write(descriptor, encoded)
        if not replaced:
            replaced = True
            os.replace(replacement, log_path)
        return written

    monkeypatch.setattr("tools.runner_secure_fs_core.os.write", replace_after_write)

    # When: the append finishes against an inode no longer named by the log path.
    with runner_secure_storage((tmp_path,)), pytest.raises(
        ContractError, match="ownership"
    ):
        secure_append(log_path, b'{"writer":"a"}\n')

    # Then: the replacement survives byte-for-byte and success is not reported.
    assert log_path.read_bytes() == replacement_bytes


def test_concurrent_confirmations_record_one_transition_and_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: two operators confirm the same awaiting run from one state snapshot.
    run_id = "RUN-confirm-race"
    state_dir = tmp_path / ".automation"
    state_path = state_dir / "state" / f"{run_id}.json"
    log_path = state_dir / "logs" / f"{run_id}.jsonl"
    state_path.parent.mkdir(parents=True)
    log_path.parent.mkdir(parents=True)
    state: JSONMap = {
        "status": RunStatus.AWAITING_USER_CONFIRMATION.value,
        "target_blog_id": "blog-fixture",
        "naver_title": "title",
        "artifact_digest": "sha256:" + "a" * 64,
        "confirmation_requested_at": "2026-09-09T09:00:00+09:00",
        "confirmation_nonce": "nonce-confirm-race",
        "confirmation_request_digest": "b" * 64,
        "stages": {"naver-rider": RunStatus.PASSED.value},
        "stage_execution": {"naver-rider": "produced"},
    }
    _ = state_path.write_text(json.dumps(state), encoding="utf-8")
    _ = log_path.write_text("", encoding="utf-8")
    first_read = threading.Event()
    second_attempt = threading.Event()
    second_read = threading.Event()
    original_read = read_runner_state
    resumes: list[str] = []
    results: list[RunnerResult] = []
    errors: list[BaseException] = []

    def controlled_read(path: Path) -> JSONMap:
        value = original_read(path)
        if threading.current_thread().name == "confirm-a":
            first_read.set()
            assert second_attempt.wait(timeout=2)
            _ = second_read.wait(timeout=0.2)
        else:
            second_read.set()
        return value

    def recovered_request(
        _state: JSONMap, root: Path, directory: Path | None
    ) -> RunnerRequest:
        return RunnerRequest(
            root=root, job="daily-generate", run_id=run_id, state_dir=directory
        )

    def resume(request: RunnerRequest) -> RunnerResult:
        _, _, lock_path = state_paths(request.root, run_id, request.state_dir)
        assert lock_path.is_file(), "confirmation lease ended before resume"
        resumes.append(request.run_id or "")
        return RunnerResult(
            run_id, RunStatus.RUNNING, state_path, log_path, (), "resumed"
        )

    def pin_request(request: RunnerRequest) -> RunnerRequest:
        return request

    monkeypatch.setattr("tools.runner_confirmation.read_state", controlled_read)
    monkeypatch.setattr(
        "tools.runner_confirmation.request_from_state", recovered_request
    )
    monkeypatch.setattr("tools.runner_confirmation.pin_daily_request", pin_request)

    def confirm(actor: str) -> None:
        if actor == "operator-b":
            second_attempt.set()
        try:
            result = apply_confirmation(
                ConfirmationInput(
                    tmp_path,
                    run_id,
                    "naver-draft-save",
                    actor=actor,
                    confirmation_nonce="nonce-confirm-race",
                ),
                resume,
            )
        except (ContractError, OSError, RunnerBlocked) as error:
            errors.append(error)
        else:
            results.append(result)

    # When: both confirmation calls overlap.
    thread_a = threading.Thread(target=confirm, args=("operator-a",), name="confirm-a")
    thread_a.start()
    assert first_read.wait(timeout=2)
    thread_b = threading.Thread(target=confirm, args=("operator-b",), name="confirm-b")
    thread_b.start()
    thread_a.join(timeout=3)
    thread_b.join(timeout=3)

    # Then: exactly one transition is resumed and exactly one event is durable.
    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert len(results) == 1
    assert len(errors) == 1
    assert resumes == [run_id]
    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["event_type"] == "confirmation"


def test_multiprocess_appends_remain_complete_jsonl(tmp_path: Path) -> None:
    # Given: four processes target the same existing runner log.
    log_path = tmp_path / "logs" / "RUN-process-race.jsonl"
    log_path.parent.mkdir()
    _ = log_path.write_text("", encoding="utf-8")
    processes = [
        multiprocessing.Process(
            target=_append_in_process,
            args=(str(tmp_path), str(log_path), writer),
        )
        for writer in range(4)
    ]

    # When: all writers append twenty events concurrently.
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)

    # Then: every process exits and all eighty records are complete JSON values.
    assert all(not process.is_alive() for process in processes)
    assert all(process.exitcode == 0 for process in processes)
    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(events) == 80
    assert {(event["writer"], event["sequence"]) for event in events} == {
        (writer, sequence) for writer in range(4) for sequence in range(20)
    }
