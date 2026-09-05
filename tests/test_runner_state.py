from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.runner_state import atomic_write_json


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
