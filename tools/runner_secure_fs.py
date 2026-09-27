from __future__ import annotations

import inspect
import os
import stat
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

from tools.contract_types import ContractError
from tools.runner_secure_fs_core import (
    READ_FLAGS,
    DirectoryIdentity,
    capture_identities,
    chain_is_current,
    close_chain,
    close_owned,
    create_owned,
    identities_are_current,
    lock_exclusive,
    open_parent,
    parent_fd,
    read_at,
    rollback,
    truncate_if_identity,
    unlink_if_identity,
    write_all,
    write_all_and_close,
)

_ACTIVE: ContextVar[bool] = ContextVar("runner_secure_storage", default=False)
_EXPECTED: ContextVar[tuple[DirectoryIdentity, ...]] = ContextVar(
    "runner_secure_expected", default=()
)


@contextmanager
def runner_secure_storage(paths: tuple[Path, ...]) -> Generator[None, None, None]:
    expected = capture_identities(paths)
    token = _ACTIVE.set(True)
    expected_token = _EXPECTED.set(expected)
    try:
        yield
    finally:
        _EXPECTED.reset(expected_token)
        _ACTIVE.reset(token)


def secure_storage_active() -> bool:
    return _ACTIVE.get()


def _open_parent(path: Path, *, create: bool):
    expected = _EXPECTED.get()
    if not identities_are_current(path, expected):
        raise ContractError("runner path changed before control-plane access")
    root_fd, chain = open_parent(path, create=create)
    if identities_are_current(path, expected):
        return root_fd, chain
    close_chain(root_fd, chain)
    raise ContractError("runner path changed during control-plane access")


def secure_entrypoint[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        bound = signature.bind(*args, **kwargs)
        first = next(iter(bound.arguments.values()))
        root = getattr(first, "root", first)
        if not isinstance(root, Path):
            raise ContractError("runner path is unsafe")
        state = bound.arguments.get("state_dir", getattr(first, "state_dir", None))
        base = state if isinstance(state, Path) else root / ".automation"
        with runner_secure_storage((root, base)):
            return function(*args, **kwargs)

    return wrapped


@dataclass(frozen=True, slots=True)
class SecureLease:
    parent_fd: int
    name: str
    device: int
    inode: int

    def release(self) -> None:
        try:
            _ = unlink_if_identity(self.parent_fd, self.name, (self.device, self.inode))
        except OSError as error:
            raise ContractError("runner lock cannot be removed safely") from error
        finally:
            os.close(self.parent_fd)


def secure_read_snapshot(path: Path) -> tuple[bytes, tuple[int, int]]:
    root_fd, chain = _open_parent(path, create=False)
    parent = parent_fd(root_fd, chain)
    try:
        descriptor = os.open(path.name, READ_FLAGS, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            encoded = handle.read()
        if not stat.S_ISREG(info.st_mode) or not chain_is_current(root_fd, chain):
            raise ContractError("runner path is unsafe")
        return encoded, (info.st_dev, info.st_ino)
    finally:
        close_chain(root_fd, chain)


def secure_read(path: Path) -> bytes:
    return secure_read_snapshot(path)[0]


def secure_atomic_replace(path: Path, encoded: bytes) -> None:
    root_fd, chain = _open_parent(path, create=True)
    parent = parent_fd(root_fd, chain)
    temporary = f".{path.name}.{uuid.uuid4().hex}"
    temporary_identity: tuple[int, int] | None = None
    try:
        previous = read_at(parent, path.name)
        descriptor, temporary_identity = create_owned(parent, temporary)
        write_all_and_close(descriptor, encoded, temporary_identity)
        if not chain_is_current(root_fd, chain):
            raise ContractError("runner path changed before state write")
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        if not chain_is_current(root_fd, chain):
            rollback(parent, path.name, encoded, previous)
            raise ContractError("runner path changed during state write")
    except OSError as error:
        raise ContractError(f"could not atomically write state: {path}") from error
    finally:
        if temporary_identity is not None:
            _ = unlink_if_identity(parent, temporary, temporary_identity)
        close_chain(root_fd, chain)


def secure_append(path: Path, encoded: bytes) -> None:
    root_fd, chain = _open_parent(path, create=True)
    parent = parent_fd(root_fd, chain)
    descriptor = -1
    identity: tuple[int, int] | None = None
    try:
        if not chain_is_current(root_fd, chain):
            raise ContractError("runner path changed before log write")
        descriptor = os.open(
            path.name,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        lock_exclusive(descriptor)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            raise ContractError("runner log ownership changed")
        before = os.fstat(descriptor)
        try:
            write_all(descriptor, encoded)
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity or not chain_is_current(
                root_fd, chain
            ):
                raise ContractError("runner log ownership changed during write")
        except (ContractError, OSError):
            truncate_if_identity(parent, path.name, descriptor, before)
            raise
    except ContractError:
        raise
    except OSError as error:
        raise ContractError(f"runner log cannot be written: {path}") from error
    finally:
        try:
            if descriptor >= 0 and identity is not None:
                close_owned(descriptor, identity)
        finally:
            close_chain(root_fd, chain)


def secure_create(path: Path, encoded: bytes) -> SecureLease:
    root_fd, chain = _open_parent(path, create=True)
    parent = parent_fd(root_fd, chain)
    try:
        if not chain_is_current(root_fd, chain):
            raise ContractError("runner path changed before lock write")
        descriptor, identity = create_owned(parent, path.name)
        try:
            write_all_and_close(descriptor, encoded, identity)
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                raise ContractError("runner lock ownership changed")
            if not chain_is_current(root_fd, chain):
                raise ContractError("runner path changed during lock write")
            retained = os.dup(parent)
            return SecureLease(retained, path.name, *identity)
        except (ContractError, OSError):
            _ = unlink_if_identity(parent, path.name, identity)
            raise
    finally:
        close_chain(root_fd, chain)


def secure_json_paths(directory: Path) -> tuple[Path, ...]:
    try:
        root_fd, chain = _open_parent(directory / ".entry", create=False)
    except ContractError:
        if not directory.exists():
            return ()
        raise
    parent = parent_fd(root_fd, chain)
    try:
        if not chain_is_current(root_fd, chain):
            raise ContractError("runner path is unsafe")
        names = sorted(name for name in os.listdir(parent) if name.endswith(".json"))
        paths: list[Path] = []
        for name in names:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ContractError("runner path is unsafe")
            paths.append(directory / name)
        if not chain_is_current(root_fd, chain):
            raise ContractError("runner path is unsafe")
        return tuple(paths)
    except OSError as error:
        raise ContractError("runner path is unsafe") from error
    finally:
        close_chain(root_fd, chain)


def secure_unlink_if_identity(path: Path, identity: tuple[int, int]) -> bool:
    root_fd, chain = _open_parent(path, create=False)
    try:
        if not chain_is_current(root_fd, chain):
            raise ContractError("runner path is unsafe")
        return unlink_if_identity(parent_fd(root_fd, chain), path.name, identity)
    except OSError as error:
        raise ContractError("runner path is unsafe") from error
    finally:
        close_chain(root_fd, chain)


__all__ = [
    "SecureLease",
    "runner_secure_storage",
    "secure_append",
    "secure_atomic_replace",
    "secure_create",
    "secure_entrypoint",
    "secure_json_paths",
    "secure_read",
    "secure_read_snapshot",
    "secure_storage_active",
    "secure_unlink_if_identity",
]
