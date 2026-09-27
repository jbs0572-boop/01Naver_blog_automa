from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.contract_types import ContractError, JSONMap
from tools.runner_state import (
    atomic_write_json,
    input_fingerprint,
    request_from_state,
    stable_run_id,
)
from tools.runner_types import RunnerRequest, TopicSelectionContext


def test_topic_selection_context_round_trips_batch_slot_and_snapshot_contract(
    tmp_path: Path,
) -> None:
    # Given
    context = TopicSelectionContext(
        "",
        "",
        "",
        "2026-09-07",
        batch_id="BATCH-a1b2c3d4e5f6",
        batch_slot=2,
        snapshot_policy="reuse_only",
        capture_id="CAPTURE-a1b2c3d4e5f6",
        snapshot_path="metadata/creator-advisor/2026-09-07/CAPTURE-a1b2c3d4e5f6.json",
        snapshot_sha256="sha256:" + "a" * 64,
        excluded_keywords=("첫 주제",),
    )
    state: JSONMap = {
        "job": "daily-generate",
        "keyword": None,
        "run_id": "RUN-context",
        "auto_topic": True,
        "dry_run": False,
        "selection_context": context.as_json(),
        "notion_target_id": None,
    }

    # When
    recovered = request_from_state(state, tmp_path)

    # Then
    assert recovered.selection_context == context


def test_stable_run_id_separates_batch_slots(tmp_path: Path) -> None:
    # Given
    first = RunnerRequest(
        tmp_path, "daily-generate", auto_topic=True,
        selection_context=TopicSelectionContext("", "", "", "2026-09-07", batch_id="BATCH-a", batch_slot=1),
    )
    second = RunnerRequest(
        tmp_path, "daily-generate", auto_topic=True,
        selection_context=TopicSelectionContext("", "", "", "2026-09-07", batch_id="BATCH-a", batch_slot=2),
    )

    # When / Then
    assert stable_run_id(first) != stable_run_id(second)


def test_post_q2_evidence_is_separate_from_hashed_producer_inputs(
    tmp_path: Path,
) -> None:
    request = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="fixture",
    )
    assets = tmp_path / "assets" / "fixture"
    assets.mkdir(parents=True)
    producer_quality = assets / "image-quality.jsonl"
    image = assets / "body.png"
    _ = producer_quality.write_text('{"quality":"passed"}\n', encoding="utf-8")
    _ = image.write_bytes(b"original-image")

    initial = input_fingerprint(request)
    q3_directory = tmp_path / "metadata" / "quality-reviews"
    q3_directory.mkdir(parents=True)
    _ = (q3_directory / "RUN-fixture-images.jsonl").write_text(
        '{"reviewed_at":"post-q2"}\n', encoding="utf-8"
    )

    assert input_fingerprint(request) == initial
    _ = producer_quality.write_text('{"quality":"changed"}\n', encoding="utf-8")
    assert input_fingerprint(request) != initial
    after_q3 = input_fingerprint(request)
    _ = (q3_directory / "RUN-fixture-images.jsonl").write_text(
        '{"reviewed_at":"refreshed-post-q2"}\n', encoding="utf-8"
    )
    assert input_fingerprint(request) == after_q3
    _ = image.write_bytes(b"changed-image")
    assert input_fingerprint(request) != after_q3


def test_request_from_state_accepts_legacy_context_without_batch_fields(tmp_path: Path) -> None:
    # Given
    state: JSONMap = {
        "job": "daily-generate",
        "keyword": "기존 주제",
        "run_id": "RUN-legacy-context",
        "auto_topic": False,
        "dry_run": False,
        "selection_context": {"as_of_date": "2026-09-07", "timezone": "Asia/Seoul"},
        "notion_target_id": None,
    }

    # When
    recovered = request_from_state(state, tmp_path)

    # Then
    assert recovered.selection_context == TopicSelectionContext("", "", "", "2026-09-07")


def test_request_from_state_preserves_current_selection_context_fingerprint(
    tmp_path: Path,
) -> None:
    selection_context = TopicSelectionContext(
        "",
        "",
        "",
        "2026-09-04",
        "Asia/Seoul",
    )
    original = RunnerRequest(
        root=tmp_path,
        job="daily-generate",
        keyword="유니클로 후드립T",
        run_id="RUN-current-context",
        auto_topic=True,
        selection_context=selection_context,
        notion_target_id="d4f6d9a1-e438-4c05-ae18-8558277c5d00",
    )
    state: JSONMap = {
        "job": original.job,
        "keyword": original.keyword,
        "run_id": original.run_id,
        "auto_topic": original.auto_topic,
        "dry_run": original.dry_run,
        "selection_context": selection_context.as_json(),
        "notion_target_id": original.notion_target_id,
    }

    recovered = request_from_state(state, tmp_path)

    assert recovered.selection_context == original.selection_context
    assert input_fingerprint(recovered) == input_fingerprint(original)




def _record_durability_operations(
    monkeypatch: pytest.MonkeyPatch,
    parent: Path,
    events: list[str],
) -> int:
    directory_fd = 91_337
    original_open = os.open
    original_fsync = os.fsync
    original_close = os.close
    original_replace = os.replace

    def record_open(
        file: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        if Path(file) == parent:
            events.append("open-directory")
            return directory_fd
        return original_open(file, flags, mode)

    def record_fsync(fd: int) -> None:
        if fd == directory_fd:
            events.append("directory-fsync")
            return
        events.append("temp-fsync")
        original_fsync(fd)

    def record_close(fd: int) -> None:
        if fd == directory_fd:
            events.append("close-directory")
            return
        original_close(fd)

    def record_replace(source: str, destination: str) -> None:
        events.append("replace")
        original_replace(source, destination)

    monkeypatch.setattr(os, "open", record_open)
    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(os, "close", record_close)
    monkeypatch.setattr(os, "replace", record_replace)
    return directory_fd


def test_atomic_write_json_syncs_parent_directory_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    path = tmp_path / "state" / "checkpoint.json"
    _ = _record_durability_operations(monkeypatch, path.parent, events)

    atomic_write_json(path, {"status": "pending"})

    assert events == [
        "temp-fsync",
        "replace",
        "open-directory",
        "directory-fsync",
        "close-directory",
    ]
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "pending"}


def test_atomic_write_json_converts_directory_sync_failure_to_contract_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    path = tmp_path / "state" / "checkpoint.json"
    path.parent.mkdir()
    _ = path.write_text(json.dumps({"status": "old"}), encoding="utf-8")
    directory_fd = _record_durability_operations(monkeypatch, path.parent, events)
    original_fsync: Callable[[int], None] = os.fsync

    def fail_directory_fsync(fd: int) -> None:
        if fd == directory_fd:
            events.append("directory-fsync-failed")
            raise OSError("directory sync failed")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_fsync)

    with pytest.raises(
        ContractError,
        match=r"^could not atomically write state: .*checkpoint\.json$",
    ):
        atomic_write_json(path, {"status": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "new"}
    assert events[-2:] == ["directory-fsync-failed", "close-directory"]


def test_atomic_write_json_converts_directory_open_failure_to_contract_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    path = tmp_path / "checkpoint.json"
    _ = path.write_text(json.dumps({"status": "old"}), encoding="utf-8")
    _ = _record_durability_operations(monkeypatch, path.parent, events)
    original_open = os.open

    def fail_directory_open(
        file: str | os.PathLike[str], flags: int, mode: int = 0o777
    ) -> int:
        if Path(file) == path.parent:
            raise OSError("directory open failed")
        return original_open(file, flags, mode)

    monkeypatch.setattr(os, "open", fail_directory_open)

    with pytest.raises(
        ContractError,
        match=r"^could not atomically write state: .*checkpoint\.json$",
    ):
        atomic_write_json(path, {"status": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "new"}


def test_atomic_write_json_converts_directory_close_failure_to_contract_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    path = tmp_path / "checkpoint.json"
    _ = _record_durability_operations(monkeypatch, path.parent, events)
    directory_fd = 91_337
    original_close = os.close

    def fail_directory_close(fd: int) -> None:
        if fd == directory_fd:
            raise OSError("directory close failed")
        original_close(fd)

    monkeypatch.setattr(os, "close", fail_directory_close)

    with pytest.raises(
        ContractError,
        match=r"^could not atomically write state: .*checkpoint\.json$",
    ):
        atomic_write_json(path, {"status": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "new"}


def test_atomic_write_json_preserves_existing_destination_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "checkpoint.json"
    _ = path.write_text(json.dumps({"status": "old"}), encoding="utf-8")

    def fail_replace(_source: str, _destination: str) -> None:
        raise OSError("rename failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(ContractError, match="could not atomically write state"):
        atomic_write_json(path, {"status": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "old"}
