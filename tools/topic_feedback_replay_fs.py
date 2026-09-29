from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError
from tools.topic_feedback_replay_binding import (
    ReplayDirectory,
    close_replay_directory,
    open_replay_directory,
    verify_replay_directory,
)
from tools.topic_feedback_replay_io import (
    close_descriptor_once,
    retry_sync,
    write_all,
)

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_COPY_ROOTS: Final = (
    "config",
    "metadata",
    "runs",
    "research",
    "drafts",
    "final",
    "assets",
)
def _scratch_write(descriptor: int, encoded: memoryview) -> int:
    return os.write(descriptor, encoded)


def _scratch_sync(descriptor: int) -> None:
    os.fsync(descriptor)


def _scratch_close(descriptor: int) -> None:
    os.close(descriptor)


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_nlink,
    )


def _read_regular(parent_fd: int, name: str, expected: os.stat_result) -> bytes:
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise ContractError("replay source contains an unsafe file") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or expected.st_nlink != 1
            or _identity(before) != _identity(expected)
        ):
            if before.st_nlink != 1 or expected.st_nlink != 1:
                raise ContractError("replay source contains a hardlinked file")
            raise ContractError("replay source changed during snapshot")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            encoded = handle.read()
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after) or len(encoded) != before.st_size:
            raise ContractError("replay source changed during snapshot")
        return encoded
    finally:
        close_descriptor_once(
            descriptor, _scratch_close, "replay source close failed safely"
        )


def _copy_directory(source_fd: int, destination: Path) -> None:
    before = os.fstat(source_fd)
    names = tuple(sorted(os.listdir(source_fd)))
    for name in names:
        try:
            metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as error:
            raise ContractError("replay source changed during snapshot") from error
        target = destination / name
        if stat.S_ISDIR(metadata.st_mode):
            try:
                child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=source_fd)
            except OSError as error:
                raise ContractError("replay source contains an unsafe path") from error
            if _identity(os.fstat(child_fd)) != _identity(metadata):
                close_descriptor_once(
                    child_fd, _scratch_close, "replay source close failed safely"
                )
                raise ContractError("replay source changed during snapshot")
            target.mkdir(mode=0o700)
            try:
                _copy_directory(child_fd, target)
            finally:
                close_descriptor_once(
                    child_fd, _scratch_close, "replay source close failed safely"
                )
        elif stat.S_ISREG(metadata.st_mode):
            encoded = _read_regular(source_fd, name, metadata)
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                write_all(descriptor, encoded, _scratch_write)
                retry_sync(
                    descriptor, _scratch_sync, "replay scratch sync failed safely"
                )
            finally:
                close_descriptor_once(
                    descriptor, _scratch_close, "replay scratch close failed safely"
                )
        else:
            raise ContractError("replay source contains a non-regular entry")
    after_names = tuple(sorted(os.listdir(source_fd)))
    after = os.fstat(source_fd)
    if names != after_names or _identity(before) != _identity(after):
        raise ContractError("replay source changed during snapshot")


@contextmanager
def replay_scratch(root_fd: int) -> Generator[Path]:
    root_before = os.fstat(root_fd)
    with tempfile.TemporaryDirectory(prefix="topic-feedback-replay-") as temporary:
        scratch = Path(temporary)
        for name in _COPY_ROOTS:
            try:
                metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise ContractError("replay source contains an unsafe root entry")
            try:
                child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=root_fd)
            except OSError as error:
                raise ContractError("replay source contains an unsafe root entry") from error
            if _identity(os.fstat(child_fd)) != _identity(metadata):
                close_descriptor_once(
                    child_fd, _scratch_close, "replay source close failed safely"
                )
                raise ContractError("replay source root changed during snapshot")
            destination = scratch / name
            destination.mkdir(mode=0o700)
            try:
                _copy_directory(child_fd, destination)
            finally:
                close_descriptor_once(
                    child_fd, _scratch_close, "replay source close failed safely"
                )
        if _identity(root_before) != _identity(os.fstat(root_fd)):
            raise ContractError("replay source root changed during snapshot")
        yield scratch


__all__ = [
    "ReplayDirectory",
    "close_descriptor_once",
    "close_replay_directory",
    "open_replay_directory",
    "replay_scratch",
    "retry_sync",
    "verify_replay_directory",
    "write_all",
]
