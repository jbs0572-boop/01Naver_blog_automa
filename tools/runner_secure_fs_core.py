from __future__ import annotations

import fcntl
import os
import stat
import uuid
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError
from tools.topic_feedback_store_fs import directory_chain_is_current

DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
type DirectoryChain = tuple[tuple[str, int], ...]
type DirectoryIdentity = tuple[Path, int, int]


def capture_identities(paths: tuple[Path, ...]) -> tuple[DirectoryIdentity, ...]:
    identities: list[DirectoryIdentity] = []
    for path in dict.fromkeys(Path(os.path.abspath(item)) for item in paths):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ContractError("runner path is unsafe")
        identities.append((path, info.st_dev, info.st_ino))
    return tuple(identities)


def identities_are_current(
    path: Path, identities: tuple[DirectoryIdentity, ...]
) -> bool:
    absolute = Path(os.path.abspath(path))
    try:
        return all(
            not absolute.is_relative_to(parent)
            or (info := parent.lstat()).st_dev == device
            and info.st_ino == inode
            for parent, device, inode in identities
        )
    except OSError:
        return False


def open_parent(path: Path, *, create: bool) -> tuple[int, DirectoryChain]:
    absolute = Path(os.path.abspath(path))
    root_path = Path(absolute.anchor)
    try:
        root_fd = os.open(root_path, DIR_FLAGS)
    except OSError as error:
        raise ContractError("runner path is unsafe") from error
    chain: list[tuple[str, int]] = []
    parent = root_fd
    try:
        for part in absolute.parts[1:-1]:
            if create:
                ensure_directory(parent, part)
            child = os.open(part, DIR_FLAGS, dir_fd=parent)
            chain.append((part, child))
            parent = child
    except OSError as error:
        close_chain(root_fd, tuple(chain))
        raise ContractError("runner path is unsafe") from error
    return root_fd, tuple(chain)


def ensure_directory(parent: int, name: str) -> None:
    try:
        os.mkdir(name, 0o755, dir_fd=parent)
        os.fsync(parent)
    except FileExistsError:
        return


def parent_fd(root_fd: int, chain: DirectoryChain) -> int:
    return chain[-1][1] if chain else root_fd


def chain_is_current(root_fd: int, chain: DirectoryChain) -> bool:
    return directory_chain_is_current((Path("/"), root_fd), chain)


def close_chain(root_fd: int, chain: DirectoryChain) -> None:
    for _, descriptor in reversed(chain):
        os.close(descriptor)
    os.close(root_fd)


def read_at(parent: int, name: str) -> bytes | None:
    try:
        descriptor = os.open(name, READ_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ContractError("runner path is unsafe") from error
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise ContractError("runner path is unsafe")
    try:
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()
    except OSError as error:
        raise ContractError("runner path is unsafe") from error


def unlink_if_identity(parent: int, name: str, identity: tuple[int, int]) -> bool:
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity:
            return False
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
        return True
    except FileNotFoundError:
        return False


def create_owned(parent: int, name: str) -> tuple[int, tuple[int, int]]:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent,
    )
    try:
        info = os.fstat(descriptor)
    except OSError:
        os.close(descriptor)
        raise
    return descriptor, (info.st_dev, info.st_ino)


def write_all(descriptor: int, encoded: bytes) -> None:
    offset = 0
    while offset < len(encoded):
        try:
            written = os.write(descriptor, encoded[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("runner write made no progress")
        offset += written
    os.fsync(descriptor)


def lock_exclusive(descriptor: int) -> None:
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return
        except InterruptedError:
            continue


def truncate_if_identity(
    parent: int, name: str, descriptor: int, before: os.stat_result
) -> None:
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    opened = os.fstat(descriptor)
    identity = (before.st_dev, before.st_ino)
    if (current.st_dev, current.st_ino) == identity == (opened.st_dev, opened.st_ino):
        os.ftruncate(descriptor, before.st_size)
        os.fsync(descriptor)


def close_owned(descriptor: int, identity: tuple[int, int]) -> None:
    try:
        os.close(descriptor)
    except OSError:
        try:
            info = os.fstat(descriptor)
        except OSError:
            info = None
        if info is not None and (info.st_dev, info.st_ino) == identity:
            os.close(descriptor)
        raise


def write_all_and_close(
    descriptor: int, encoded: bytes, identity: tuple[int, int]
) -> None:
    try:
        write_all(descriptor, encoded)
    finally:
        close_owned(descriptor, identity)


def rollback(parent: int, name: str, encoded: bytes, previous: bytes | None) -> None:
    try:
        descriptor = os.open(name, READ_FLAGS, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            owned = handle.read() == encoded
        if not owned:
            return
        if previous is None:
            _ = unlink_if_identity(parent, name, (info.st_dev, info.st_ino))
            return
        temporary = f".{name}.rollback-{uuid.uuid4().hex}"
        descriptor, restored = create_owned(parent, temporary)
        try:
            write_all_and_close(descriptor, previous, restored)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            _ = unlink_if_identity(parent, temporary, restored)
    except OSError as error:
        raise ContractError("runner state rollback failed") from error


__all__ = [
    "READ_FLAGS",
    "DirectoryChain",
    "DirectoryIdentity",
    "capture_identities",
    "chain_is_current",
    "close_chain",
    "close_owned",
    "create_owned",
    "identities_are_current",
    "lock_exclusive",
    "open_parent",
    "parent_fd",
    "read_at",
    "rollback",
    "truncate_if_identity",
    "unlink_if_identity",
    "write_all",
    "write_all_and_close",
]
