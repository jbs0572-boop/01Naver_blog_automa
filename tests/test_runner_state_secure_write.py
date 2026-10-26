from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Self

import pytest

from tools import runner_state
from tools.contract_types import ContractError


def test_atomic_state_write_rejects_parent_swap_and_restores_previous_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "state"
    parent.mkdir()
    target = parent / "run.json"
    _ = target.write_bytes(b"previous state")
    moved_parent = tmp_path / "moved-state"
    original_replace = os.replace
    swapped = False

    def swap_parent_after_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if not swapped:
            swapped = True
            _ = parent.rename(moved_parent)
            _ = parent.mkdir()
            _ = (parent / "attacker-marker").write_bytes(b"leave intact")

    monkeypatch.setattr(os, "replace", swap_parent_after_replace)

    with pytest.raises(ContractError, match="path changed during state write"):
        runner_state.atomic_write_json(target, {"status": "passed"})

    assert (moved_parent / target.name).read_bytes() == b"previous state"
    assert not target.exists()
    assert (parent / "attacker-marker").read_bytes() == b"leave intact"


def test_atomic_state_write_rejects_symlinked_parent(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ContractError, match="path is unsafe"):
        runner_state.atomic_write_json(alias / "run.json", {"status": "passed"})

    assert not (actual / "run.json").exists()


def test_atomic_state_write_keeps_existing_json_contract(tmp_path: Path) -> None:
    target = tmp_path / "new" / "run.json"

    runner_state.atomic_write_json(target, {"status": "passed", "label": "검증"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "label": "검증",
        "status": "passed",
    }


def test_temp_write_failure_does_not_close_reused_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    original_fdopen = os.fdopen
    unrelated_fd: int | None = None

    class ClosedWriterThenRaises:
        def __init__(self, descriptor: int, mode: str) -> None:
            self.handle = original_fdopen(descriptor, mode)

        def __enter__(self) -> Self:
            return self

        def write(self, data: bytes) -> int:
            return self.handle.write(data)

        def flush(self) -> None:
            self.handle.flush()

        def fileno(self) -> int:
            return self.handle.fileno()

        def __exit__(self, *_: object) -> None:
            nonlocal unrelated_fd
            self.handle.close()
            unrelated_fd = os.open("/dev/null", os.O_RDONLY)
            raise OSError("simulated flush failure after close")

    monkeypatch.setattr(os, "fdopen", ClosedWriterThenRaises)
    try:
        with pytest.raises(OSError, match="simulated flush failure"):
            runner_state._write_state_temp(parent_fd, ".state.tmp", b"{}")
        assert unrelated_fd is not None
        assert os.fstat(unrelated_fd)
        assert list(tmp_path.iterdir()) == []
    finally:
        if unrelated_fd is not None:
            os.close(unrelated_fd)
        os.close(parent_fd)


def test_temp_write_fstat_failure_closes_descriptor_and_removes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def fail_fstat(descriptor: int) -> os.stat_result:
        _ = descriptor
        raise OSError("simulated fstat failure")

    monkeypatch.setattr(os, "fstat", fail_fstat)
    try:
        with pytest.raises(OSError, match="simulated fstat failure"):
            runner_state._write_state_temp(parent_fd, ".state.tmp", b"{}")
        assert list(tmp_path.iterdir()) == []
    finally:
        os.close(parent_fd)
