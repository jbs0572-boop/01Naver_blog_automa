from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW
_CREATE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


def open_root(root: Path) -> int:
    if root.is_symlink():
        raise ContractError("snapshot store path is unsafe")
    try:
        return os.open(root, _DIRECTORY_FLAGS)
    except OSError as error:
        raise ContractError("snapshot root must be an existing directory") from error


def open_directory(parent_fd: int, name: str, *, create: bool) -> int:
    if create:
        _create_directory(parent_fd, name)
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise ContractError("snapshot store path is unsafe") from error
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ContractError("snapshot store path is unsafe")
    return descriptor


def read_file(parent_fd: int, name: str) -> bytes:
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise ContractError("snapshot is unreadable or unsafe") from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ContractError("snapshot is unreadable or unsafe")
    try:
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()
    except OSError as error:
        raise ContractError("snapshot is unreadable or unsafe") from error


def publish_exclusive(parent_fd: int, name: str, encoded: bytes) -> None:
    temporary = f".tmp-{uuid.uuid4().hex}"
    created = False
    try:
        descriptor = os.open(temporary, _CREATE_FLAGS, 0o644, dir_fd=parent_fd)
        created = True
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            os.fsync(parent_fd)
        except FileExistsError:
            raise ContractError("snapshot is append-only") from None
    except ContractError:
        raise
    except OSError as error:
        raise ContractError("snapshot write failed safely") from error
    finally:
        if created:
            unlink_if_present(parent_fd, temporary)


def unlink_if_present(parent_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return


def _create_directory(parent_fd: int, name: str) -> None:
    try:
        os.mkdir(name, 0o755, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        return
    except OSError as error:
        raise ContractError("snapshot store path is unsafe") from error


def directory_chain_is_current(
    root: tuple[Path, int],
    chain: tuple[tuple[str, int], ...],
) -> bool:
    root_path, root_fd = root
    try:
        root_stat = os.stat(root_path, follow_symlinks=False)
        if not _same_directory(root_stat, os.fstat(root_fd)):
            return False
        parents = (root_fd, *(descriptor for _, descriptor in chain[:-1]))
        return all(
            _same_directory(
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False),
                os.fstat(descriptor),
            )
            for parent_fd, (name, descriptor) in zip(parents, chain, strict=True)
        )
    except OSError:
        return False


def _same_directory(first: os.stat_result, second: os.stat_result) -> bool:
    return stat.S_ISDIR(first.st_mode) and (first.st_dev, first.st_ino) == (
        second.st_dev,
        second.st_ino,
    )
