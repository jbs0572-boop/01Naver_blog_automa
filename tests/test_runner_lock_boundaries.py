"""Failure-mode regression coverage for the runner's exclusive lock."""

import json
import os
from pathlib import Path

import pytest

from tools import runner_lock
from tools.contract_types import ContractError
from tools.runner_types import RunnerBlocked


def test_nonpositive_pid_is_not_a_running_process(monkeypatch):
    def fail_if_called(*_args):
        pytest.fail("nonpositive PID must not be probed")
    monkeypatch.setattr(runner_lock.os, "kill", fail_if_called)
    assert runner_lock._pid_alive(0) is False
    assert runner_lock._pid_alive(-100) is False


@pytest.mark.parametrize(
    ("raised", "expected"),
    [(ProcessLookupError(), False), (PermissionError(), True), (OSError(), True), (None, True)],
)
def test_pid_probe_distinguishes_missing_from_inaccessible_processes(monkeypatch, raised, expected):
    def probe(pid, signal):
        assert (pid, signal) == (54321, 0)
        if raised is not None:
            raise raised
    monkeypatch.setattr(runner_lock.os, "kill", probe)
    assert runner_lock._pid_alive(54321) is expected


@pytest.mark.parametrize(
    ("contents", "expected"),
    [('{"pid": 42}', 42), ('{"pid": true}', None), ('{"pid": "42"}', None),
     ('{"pid": 4.2}', None), ('{"who": 42}', None), ('[42]', None), ('null', None)],
)
def test_existing_pid_accepts_only_json_integer(tmp_path, contents, expected):
    path = tmp_path / "lock.json"
    path.write_text(contents, encoding="utf-8")
    assert runner_lock._existing_pid(path) == expected


@pytest.mark.parametrize("contents", ["{bad-json", ""])
def test_corrupt_lock_fails_closed(tmp_path, contents):
    path = tmp_path / "lock.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ContractError, match="runner lock cannot be read"):
        runner_lock._existing_pid(path)


def test_missing_lock_file_fails_closed(tmp_path):
    with pytest.raises(ContractError, match="runner lock cannot be read"):
        runner_lock._existing_pid(tmp_path / "missing")


def test_lock_is_exclusive_writes_metadata_and_cleans_up(tmp_path):
    path = tmp_path / "state" / "runner.lock"
    with runner_lock.acquire_lock(path, {"pid": os.getpid(), "run_id": "one"}):
        assert json.loads(path.read_text(encoding="utf-8")) == {"pid": os.getpid(), "run_id": "one"}
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(RunnerBlocked, match="already locked"):
            with runner_lock.acquire_lock(path, {"pid": os.getpid()}):
                pytest.fail("concurrent run must be blocked")
    assert not path.exists()


def test_stale_lock_is_replaced_and_exception_still_releases_lock(tmp_path, monkeypatch):
    path = tmp_path / "runner.lock"
    path.write_text('{"pid": 987654}', encoding="utf-8")
    monkeypatch.setattr(runner_lock, "_pid_alive", lambda _pid: False)
    with pytest.raises(ValueError, match="work failed"):
        with runner_lock.acquire_lock(path, {"pid": os.getpid()}):
            assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()
            raise ValueError("work failed")
    assert not path.exists()


def test_unreadable_existing_lock_is_not_removed(tmp_path):
    path = tmp_path / "runner.lock"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ContractError, match="cannot be read"):
        with runner_lock.acquire_lock(path, {"pid": 1}):
            pytest.fail("invalid lock should block")
    assert path.read_text(encoding="utf-8") == "not-json"


def test_lock_creation_error_has_specific_diagnostic(tmp_path, monkeypatch):
    def fail_open(*_args, **_kwargs):
        raise PermissionError("permission denied")
    monkeypatch.setattr(runner_lock.os, "open", fail_open)
    with pytest.raises(ContractError, match="runner lock cannot be created"):
        with runner_lock.acquire_lock(tmp_path / "runner.lock", {"pid": 1}):
            pytest.fail("failed creation cannot enter critical section")


def test_lock_write_error_removes_incomplete_file(tmp_path, monkeypatch):
    path = tmp_path / "runner.lock"
    def fail_fsync(_fd):
        raise OSError("storage error")
    monkeypatch.setattr(runner_lock.os, "fsync", fail_fsync)
    with pytest.raises(ContractError, match="runner lock cannot be written"):
        with runner_lock.acquire_lock(path, {"pid": 1}):
            pytest.fail("failed write cannot enter critical section")
    assert not path.exists()


def test_stale_lock_removal_error_is_not_silently_ignored(tmp_path, monkeypatch):
    path = tmp_path / "runner.lock"
    path.write_text('{"pid": -3}', encoding="utf-8")
    original_unlink = Path.unlink
    def deny_unlink(self, *args, **kwargs):
        if self == path:
            raise PermissionError("denied")
        return original_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", deny_unlink)
    with pytest.raises(ContractError, match="stale runner lock cannot be removed"):
        with runner_lock.acquire_lock(path, {"pid": 1}):
            pytest.fail("could not remove stale lock")


def test_lock_cleanup_error_prevents_false_success(tmp_path, monkeypatch):
    path = tmp_path / "runner.lock"
    original_unlink = Path.unlink
    def deny_unlink(self, *args, **kwargs):
        if self == path:
            raise PermissionError("denied")
        return original_unlink(self, *args, **kwargs)
    with pytest.raises(ContractError, match="runner lock cleanup failed"):
        with runner_lock.acquire_lock(path, {"pid": os.getpid()}):
            monkeypatch.setattr(Path, "unlink", deny_unlink)
    assert path.exists()  # Cannot claim the completed job released its lock.


def test_disappearing_stale_lock_retries_exclusive_create(tmp_path, monkeypatch):
    path = tmp_path / "runner.lock"
    path.write_text('{"pid": -1}', encoding="utf-8")
    original_unlink = Path.unlink
    calls = 0

    def disappear_once(self, *args, **kwargs):
        nonlocal calls
        if self == path and calls == 0:
            calls += 1
            original_unlink(self)
            raise FileNotFoundError("removed by competing process")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", disappear_once)
    with runner_lock.acquire_lock(path, {"pid": os.getpid()}):
        assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert calls == 1
    assert not path.exists()


def test_cleanup_failure_does_not_mask_original_write_failure(tmp_path, monkeypatch):
    path = tmp_path / "runner.lock"
    original_unlink = Path.unlink

    def deny_unlink(self, *args, **kwargs):
        if self == path:
            raise OSError("cleanup failed")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(runner_lock.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("disk failed")))
    monkeypatch.setattr(Path, "unlink", deny_unlink)
    with pytest.raises(ContractError, match="runner lock cannot be written"):
        with runner_lock.acquire_lock(path, {"pid": 1}):
            pytest.fail("write failure must prevent entry")


def test_lock_cleanup_failure_preserves_original_error_with_note(tmp_path, monkeypatch):
    path = tmp_path / "runner.lock"
    original_unlink = Path.unlink

    def deny_unlink(self, *args, **kwargs):
        if self == path:
            raise PermissionError("cleanup denied")
        return original_unlink(self, *args, **kwargs)

    with pytest.raises(ValueError, match="original worker error") as raised:
        with runner_lock.acquire_lock(path, {"pid": os.getpid()}):
            monkeypatch.setattr(Path, "unlink", deny_unlink)
            raise ValueError("original worker error")
    assert any("runner lock cleanup failed" in note for note in raised.value.__notes__)
