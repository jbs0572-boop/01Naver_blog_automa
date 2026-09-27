from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass

from tools.contract_types import ContractError
from tools.topic_feedback_replay_io import close_descriptor_once

type CloseOperation = Callable[[int], None]


@dataclass(frozen=True, slots=True)
class VerifiedFile:
    descriptor: int
    identity: tuple[int, int, int]


@contextmanager
def locked_descriptor(descriptor: int) -> Generator[None]:
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                break
            except InterruptedError:
                continue
    except OSError as error:
        raise ContractError("replay evidence lock failed safely") from error
    try:
        yield
    finally:
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    break
                except InterruptedError:
                    continue
        except OSError as error:
            raise ContractError("replay evidence unlock failed safely") from error


def _state(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _verified_bytes(descriptor: int, label: str) -> tuple[bytes, tuple[int, int, int]]:
    try:
        _ = os.lseek(descriptor, 0, os.SEEK_SET)
        before = os.fstat(descriptor)
    except OSError as error:
        raise ContractError(f"{label} changed") from error
    if not stat.S_ISREG(before.st_mode):
        raise ContractError(f"{label} is unsafe")
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(descriptor, 65536)
        except InterruptedError:
            continue
        except OSError as error:
            raise ContractError(f"{label} changed") from error
        if not chunk:
            break
        chunks.append(chunk)
    encoded = b"".join(chunks)
    try:
        after = os.fstat(descriptor)
    except OSError as error:
        raise ContractError(f"{label} changed") from error
    if _state(before) != _state(after) or len(encoded) != before.st_size:
        raise ContractError(f"{label} changed")
    return encoded, (before.st_dev, before.st_ino, before.st_size)


def open_verified_file(
    parent_fd: int,
    name: str,
    encoded: bytes,
    label: str,
    close_operation: CloseOperation,
) -> VerifiedFile:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise ContractError(f"{label} collision") from error
    try:
        return verify_descriptor(descriptor, encoded, label)
    except ContractError:
        close_descriptor_once(
            descriptor, close_operation, "replay evidence close failed safely"
        )
        raise


def verify_descriptor(descriptor: int, encoded: bytes, label: str) -> VerifiedFile:
    actual, identity = _verified_bytes(descriptor, label)
    if actual != encoded:
        raise ContractError(f"{label} collision")
    return VerifiedFile(descriptor, identity)


def verify_file_path(
    parent_fd: int, name: str, verified: VerifiedFile, label: str
) -> None:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise ContractError(f"{label} changed") from error
    if (named.st_dev, named.st_ino, named.st_size) != verified.identity:
        raise ContractError(f"{label} changed")


def reverify_file(verified: VerifiedFile, encoded: bytes, label: str) -> None:
    actual, identity = _verified_bytes(verified.descriptor, label)
    if identity != verified.identity or actual != encoded:
        raise ContractError(f"{label} changed")


__all__ = [
    "VerifiedFile",
    "locked_descriptor",
    "open_verified_file",
    "reverify_file",
    "verify_descriptor",
    "verify_file_path",
]
