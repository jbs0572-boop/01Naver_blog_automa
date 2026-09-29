from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.contract_types import ContractError
from tools.runner_execution import (
    confirm_job,
    get_status,
    recover_job,
    resume_job,
    run_job,
)
from tools.runner_lock import acquire_lock
from tools.runner_secure_fs import (
    secure_atomic_replace,
    secure_create,
    secure_unlink_if_identity,
)
from tools.runner_types import ConfirmationInput, RunnerBlocked, RunnerRequest


def test_stale_lock_cleanup_does_not_delete_replacement_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "locks/run.lock"
    lock.parent.mkdir()
    _ = lock.write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    original_unlink = secure_unlink_if_identity

    def replace_before_unlink(path: Path, identity: tuple[int, int]) -> bool:
        replacement = path.with_name(f"{path.name}.replacement")
        _ = replacement.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        path.unlink()
        os.replace(replacement, path)
        return original_unlink(path, identity)

    monkeypatch.setattr(
        "tools.runner_lock.secure_unlink_if_identity", replace_before_unlink
    )

    with pytest.raises(RunnerBlocked), acquire_lock(lock, {"pid": os.getpid()}):
        pytest.fail("replacement owner was deleted")


def test_secure_create_cleanup_preserves_replacement_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "locks/run.lock"
    calls = 0

    def replace_on_second_check(_root: int, _chain: tuple[tuple[str, int], ...]) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return True
        replacement = lock.with_name(f"{lock.name}.replacement")
        _ = replacement.write_bytes(b"replacement")
        lock.unlink()
        os.replace(replacement, lock)
        return False

    monkeypatch.setattr(
        "tools.runner_secure_fs.chain_is_current", replace_on_second_check
    )

    with pytest.raises(ContractError, match="runner path changed"):
        _ = secure_create(lock, b"owned")
    assert lock.read_bytes() == b"replacement"


def test_secure_create_captures_owner_before_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "locks/run.lock"
    replaced = False
    original_stat = os.stat

    def replace_before_path_stat(
        path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replaced
        if path == lock.name and dir_fd is not None and not replaced:
            replaced = True
            replacement = lock.with_name(f"{lock.name}.replacement")
            _ = replacement.write_bytes(b"replacement")
            lock.unlink()
            os.replace(replacement, lock)
        return original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr("tools.runner_secure_fs.os.stat", replace_before_path_stat)

    with pytest.raises(ContractError, match="ownership"):
        _ = secure_create(lock, b"owned")
    assert lock.read_bytes() == b"replacement"


@pytest.mark.parametrize("operation", ["lock", "state"])
def test_partial_write_cleans_only_owned_leaf_without_fd_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    destination = tmp_path / operation / "run.json"
    destination.parent.mkdir()
    descriptors_before = len(os.listdir("/dev/fd"))
    original_write = os.write
    failed = False

    def partial_write(descriptor: int, encoded: bytes) -> int:
        nonlocal failed
        if not failed:
            failed = True
            _ = original_write(descriptor, encoded[:1])
            raise OSError("injected partial write")
        return original_write(descriptor, encoded)

    monkeypatch.setattr("tools.runner_secure_fs.os.write", partial_write)

    error = OSError if operation == "lock" else ContractError
    with pytest.raises(error, match="partial write|atomically write"):
        if operation == "lock":
            _ = secure_create(destination, b"owned")
        else:
            secure_atomic_replace(destination, b"owned")

    assert list(destination.parent.iterdir()) == []
    assert len(os.listdir("/dev/fd")) == descriptors_before


@pytest.mark.parametrize("operation", ["lock", "state"])
def test_partial_write_cleanup_preserves_replacement_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    destination = tmp_path / operation / "run.json"
    destination.parent.mkdir()
    descriptors_before = len(os.listdir("/dev/fd"))
    original_write = os.write
    failed = False

    def replace_after_partial_write(descriptor: int, encoded: bytes) -> int:
        nonlocal failed
        if not failed:
            failed = True
            _ = original_write(descriptor, encoded[:1])
            leaf = next(destination.parent.iterdir())
            leaf.unlink()
            _ = leaf.write_bytes(b"replacement")
            raise OSError("injected partial write")
        return original_write(descriptor, encoded)

    monkeypatch.setattr("tools.runner_secure_fs.os.write", replace_after_partial_write)
    error = OSError if operation == "lock" else ContractError
    with pytest.raises(error, match="partial write|atomically write"):
        if operation == "lock":
            _ = secure_create(destination, b"owned")
        else:
            secure_atomic_replace(destination, b"owned")

    replacement = next(destination.parent.iterdir())
    assert replacement.read_bytes() == b"replacement"
    assert len(os.listdir("/dev/fd")) == descriptors_before


def test_run_job_accepts_keyword_invocation(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="unsupported"):
        _ = run_job(request=RunnerRequest(tmp_path, "unknown"))


def test_recover_job_accepts_keyword_invocation(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="recover requires"):
        _ = recover_job(request=RunnerRequest(tmp_path, ""))


def test_resume_job_accepts_keyword_invocation(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="resume requires"):
        _ = resume_job(request=RunnerRequest(tmp_path, ""))


def test_get_status_accepts_keyword_invocation(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="runner path|runner state"):
        _ = get_status(root=tmp_path, run_id="RUN-missing")


def test_get_status_accepts_mixed_invocation(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="runner path|runner state"):
        _ = get_status(tmp_path, run_id="RUN-missing")


def test_confirm_job_accepts_keyword_invocation(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="could not read runner state"):
        _ = confirm_job(
            confirmation_input=ConfirmationInput(
                tmp_path, "RUN-missing", "naver-draft-save"
            )
        )
