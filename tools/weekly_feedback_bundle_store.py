from __future__ import annotations

import fcntl
import os
import stat
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from tools.contract_types import ContractError
from tools.topic_feedback_store_fs import (
    directory_chain_is_current,
    open_directory,
    open_root,
    read_file,
    unlink_if_present,
)

_CREATE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
type BundleFault = Callable[[int, str], None]


class BundleOutputLike(Protocol):
    @property
    def relative_path(self) -> Path: ...

    @property
    def encoded(self) -> bytes: ...


@dataclass(frozen=True, slots=True)
class _StagedOutput:
    chain: tuple[tuple[str, int], ...]
    parent_fd: int
    temporary: str
    destination: str
    device: int
    inode: int
    encoded: bytes


def publish_bundle_at(
    root: Path,
    outputs: Sequence[BundleOutputLike],
    fault: BundleFault | None = None,
) -> None:
    root_fd = open_root(root)
    staged: list[_StagedOutput] = []
    published: list[_StagedOutput] = []
    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        for index, output in enumerate(outputs):
            candidate = _stage_output(root, root_fd, output)
            if candidate is None:
                continue
            staged.append(candidate)
            if fault is not None:
                fault(index, "stage")
        for index, candidate in enumerate(staged):
            if fault is not None:
                fault(index, "publish")
            if not directory_chain_is_current((root, root_fd), candidate.chain):
                raise ContractError("feedback bundle path changed during write")
            os.link(
                candidate.temporary,
                candidate.destination,
                src_dir_fd=candidate.parent_fd,
                dst_dir_fd=candidate.parent_fd,
                follow_symlinks=False,
            )
            published.append(candidate)
            os.fsync(candidate.parent_fd)
        if any(
            not directory_chain_is_current((root, root_fd), candidate.chain)
            for candidate in published
        ):
            raise ContractError("feedback bundle path changed during write")
    except ContractError:
        _rollback(published)
        raise
    except OSError as error:
        _rollback(published)
        raise ContractError("feedback bundle write failed safely") from error
    finally:
        for candidate in staged:
            unlink_if_present(candidate.parent_fd, candidate.temporary)
            _close_chain(candidate.chain)
        fcntl.flock(root_fd, fcntl.LOCK_UN)
        os.close(root_fd)


def _stage_output(
    root: Path,
    root_fd: int,
    output: BundleOutputLike,
) -> _StagedOutput | None:
    parts = output.relative_path.parts
    if output.relative_path.is_absolute() or not parts or any(
        part in {"", ".", ".."} for part in parts
    ):
        raise ContractError("feedback bundle destination is unsafe")
    chain = _open_chain(root_fd, parts[:-1])
    parent_fd = chain[-1][1] if chain else root_fd
    try:
        existing = _read_existing(parent_fd, parts[-1])
        if existing is not None:
            if existing != output.encoded:
                raise ContractError("feedback bundle is append-only")
            _close_chain(chain)
            return None
        if not directory_chain_is_current((root, root_fd), chain):
            raise ContractError("feedback bundle path changed during write")
        temporary = f".tmp-{uuid.uuid4().hex}"
        descriptor = os.open(
            temporary,
            _CREATE_FLAGS,
            0o644,
            dir_fd=parent_fd,
        )
        info = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                _ = handle.write(output.encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            unlink_if_present(parent_fd, temporary)
            raise
        return _StagedOutput(
            chain,
            parent_fd,
            temporary,
            parts[-1],
            info.st_dev,
            info.st_ino,
            output.encoded,
        )
    except (OSError, ContractError):
        _close_chain(chain)
        raise


def _open_chain(root_fd: int, parts: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    chain: list[tuple[str, int]] = []
    parent_fd = root_fd
    try:
        for part in parts:
            descriptor = open_directory(parent_fd, part, create=True)
            chain.append((part, descriptor))
            parent_fd = descriptor
    except ContractError:
        _close_chain(tuple(chain))
        raise
    return tuple(chain)


def _read_existing(parent_fd: int, name: str) -> bytes | None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ContractError("feedback bundle destination is unsafe")
    return read_file(parent_fd, name)


def _rollback(published: list[_StagedOutput]) -> None:
    for candidate in reversed(published):
        try:
            info = os.stat(
                candidate.destination,
                dir_fd=candidate.parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        if (
            (info.st_dev, info.st_ino) == (candidate.device, candidate.inode)
            and read_file(candidate.parent_fd, candidate.destination)
            == candidate.encoded
        ):
            os.unlink(candidate.destination, dir_fd=candidate.parent_fd)
            os.fsync(candidate.parent_fd)


def _close_chain(chain: tuple[tuple[str, int], ...]) -> None:
    for _, descriptor in reversed(chain):
        os.close(descriptor)


__all__ = ["BundleFault", "BundleOutputLike", "publish_bundle_at"]
