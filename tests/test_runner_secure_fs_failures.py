from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.runner_secure_fs import secure_append, secure_atomic_replace, secure_create


def _fd_count() -> int:
    return len(os.listdir("/dev/fd"))


def _regular_fd(descriptor: int) -> bool:
    try:
        return stat.S_ISREG(os.fstat(descriptor).st_mode)
    except OSError:
        return False


def test_append_retries_interrupted_one_byte_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "logs/run.jsonl"
    log.parent.mkdir()
    _ = log.write_bytes(b"old\n")
    original_write = os.write
    calls = 0

    def interrupted_short_write(descriptor: int, encoded: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("injected EINTR")
        return original_write(descriptor, encoded[:1])

    monkeypatch.setattr("tools.runner_secure_fs.os.write", interrupted_short_write)
    secure_append(log, b'{"event":1}\n')

    assert log.read_bytes() == b'old\n{"event":1}\n'
    assert calls > 2


def test_append_zero_progress_restores_original_and_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "logs/run.jsonl"
    log.parent.mkdir()
    _ = log.write_bytes(b"old\n")
    before = _fd_count()

    def zero_write(_descriptor: int, _encoded: bytes) -> int:
        return 0

    monkeypatch.setattr("tools.runner_secure_fs.os.write", zero_write)

    with pytest.raises(ContractError, match="log cannot be written"):
        secure_append(log, b'{"event":1}\n')

    assert log.read_bytes() == b"old\n"
    assert _fd_count() == before


def test_append_partial_exception_restores_original_and_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "logs/run.jsonl"
    log.parent.mkdir()
    _ = log.write_bytes(b"old\n")
    original_write = os.write
    calls = 0
    before = _fd_count()

    def partial_then_error(descriptor: int, encoded: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, encoded[:1])
        raise OSError("injected partial failure")

    monkeypatch.setattr("tools.runner_secure_fs.os.write", partial_then_error)
    with pytest.raises(ContractError, match="log cannot be written"):
        secure_append(log, b'{"event":1}\n')

    assert log.read_bytes() == b"old\n"
    assert _fd_count() == before


def test_append_fsync_failure_restores_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "logs/run.jsonl"
    log.parent.mkdir()
    _ = log.write_bytes(b"old\n")
    original_fsync = os.fsync
    failed = False

    def fail_regular_fsync(descriptor: int) -> None:
        nonlocal failed
        if _regular_fd(descriptor) and not failed:
            failed = True
            raise OSError("injected fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr("tools.runner_secure_fs.os.fsync", fail_regular_fsync)
    with pytest.raises(ContractError, match="log cannot be written"):
        secure_append(log, b'{"event":1}\n')
    assert log.read_bytes() == b"old\n"


@pytest.mark.parametrize("after_real_close", [False, True])
def test_append_close_failure_surfaces_without_fd_leak_or_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_real_close: bool
) -> None:
    log = tmp_path / "logs/run.jsonl"
    log.parent.mkdir()
    _ = log.write_bytes(b"old\n")
    original_close = os.close
    failed = False
    before = _fd_count()

    def fail_regular_close(descriptor: int) -> None:
        nonlocal failed
        if _regular_fd(descriptor) and not failed:
            failed = True
            if after_real_close:
                original_close(descriptor)
            raise OSError("injected close failure")
        original_close(descriptor)

    monkeypatch.setattr("tools.runner_secure_fs.os.close", fail_regular_close)
    with pytest.raises(OSError, match="injected close failure"):
        secure_append(log, b'{"event":1}\n')

    assert log.read_bytes() == b'old\n{"event":1}\n'
    assert _fd_count() == before


def test_append_failure_never_truncates_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "logs/run.jsonl"
    log.parent.mkdir()
    _ = log.write_bytes(b"old\n")
    original_write = os.write

    def replace_after_partial(descriptor: int, encoded: bytes) -> int:
        _ = original_write(descriptor, encoded[:1])
        log.unlink()
        _ = log.write_bytes(b"replacement\n")
        raise OSError("injected replacement failure")

    monkeypatch.setattr("tools.runner_secure_fs.os.write", replace_after_partial)
    with pytest.raises(ContractError, match="log cannot be written"):
        secure_append(log, b'{"event":1}\n')
    assert log.read_bytes() == b"replacement\n"


def test_append_close_failure_preserves_replacement_and_closes_owned_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "logs/run.jsonl"
    log.parent.mkdir()
    _ = log.write_bytes(b"old\n")
    original_close = os.close
    failed = False
    before = _fd_count()

    def replace_before_failed_close(descriptor: int) -> None:
        nonlocal failed
        if _regular_fd(descriptor) and not failed:
            failed = True
            log.unlink()
            _ = log.write_bytes(b"replacement\n")
            raise OSError("injected close failure")
        original_close(descriptor)

    monkeypatch.setattr("tools.runner_secure_fs.os.close", replace_before_failed_close)
    with pytest.raises(OSError, match="injected close failure"):
        secure_append(log, b'{"event":1}\n')
    assert log.read_bytes() == b"replacement\n"
    assert _fd_count() == before


@pytest.mark.parametrize("operation", ["lock", "state"])
@pytest.mark.parametrize("boundary", ["fsync", "close"])
def test_owned_create_failure_cleans_artifact_and_closes_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    boundary: str,
) -> None:
    destination = tmp_path / operation / "run.json"
    destination.parent.mkdir()
    original_fsync = os.fsync
    original_close = os.close
    failed = False
    before = _fd_count()

    def fail_fsync(descriptor: int) -> None:
        nonlocal failed
        if boundary == "fsync" and _regular_fd(descriptor) and not failed:
            failed = True
            raise OSError("injected fsync failure")
        original_fsync(descriptor)

    def fail_close(descriptor: int) -> None:
        nonlocal failed
        if boundary == "close" and _regular_fd(descriptor) and not failed:
            failed = True
            raise OSError("injected close failure")
        original_close(descriptor)

    monkeypatch.setattr("tools.runner_secure_fs.os.fsync", fail_fsync)
    monkeypatch.setattr("tools.runner_secure_fs.os.close", fail_close)
    error = OSError if operation == "lock" else ContractError
    with pytest.raises(error, match="injected|atomically write"):
        if operation == "lock":
            _ = secure_create(destination, b"owned")
        else:
            secure_atomic_replace(destination, b"owned")
    assert list(destination.parent.iterdir()) == []
    assert _fd_count() == before


@pytest.mark.parametrize("operation", ["lock", "state"])
@pytest.mark.parametrize("boundary", ["fsync", "close"])
def test_create_failures_preserve_replacement_and_close_owned_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    boundary: str,
) -> None:
    destination = tmp_path / operation / "run.json"
    destination.parent.mkdir()
    original_fsync = os.fsync
    original_close = os.close
    failed = False
    before = _fd_count()

    def replace_leaf() -> None:
        leaf = next(destination.parent.iterdir())
        leaf.unlink()
        _ = leaf.write_bytes(b"replacement")

    def fail_fsync(descriptor: int) -> None:
        nonlocal failed
        if boundary == "fsync" and _regular_fd(descriptor) and not failed:
            failed = True
            replace_leaf()
            raise OSError("injected fsync failure")
        original_fsync(descriptor)

    def fail_close(descriptor: int) -> None:
        nonlocal failed
        if boundary == "close" and _regular_fd(descriptor) and not failed:
            failed = True
            replace_leaf()
            raise OSError("injected close failure")
        original_close(descriptor)

    monkeypatch.setattr("tools.runner_secure_fs.os.fsync", fail_fsync)
    monkeypatch.setattr("tools.runner_secure_fs.os.close", fail_close)
    error = OSError if operation == "lock" else ContractError
    with pytest.raises(error, match="injected|atomically write"):
        if operation == "lock":
            _ = secure_create(destination, b"owned")
        else:
            secure_atomic_replace(destination, b"owned")

    replacement = next(destination.parent.iterdir())
    assert replacement.read_bytes() == b"replacement"
    assert _fd_count() == before
