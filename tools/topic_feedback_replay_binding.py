from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError
from tools.topic_feedback_replay_io import close_descriptor_once

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
type InodeIdentity = tuple[int, int]


@dataclass(frozen=True, slots=True)
class ReplayDirectory:
    path: Path
    descriptor: int
    parent_descriptor: int
    leaf_name: str
    component_identities: tuple[InodeIdentity, ...]


def _close(descriptor: int) -> None:
    os.close(descriptor)


def _inode(metadata: os.stat_result) -> InodeIdentity:
    return metadata.st_dev, metadata.st_ino


def open_replay_directory(path: Path, label: str) -> ReplayDirectory:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts[1:]
    if not parts:
        raise ContractError(f"{label} must be an existing safe directory")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    identities = [_inode(os.fstat(descriptor))]
    try:
        for index, part in enumerate(parts):
            next_descriptor = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            identities.append(_inode(os.fstat(next_descriptor)))
            if index == len(parts) - 1:
                return ReplayDirectory(
                    absolute,
                    next_descriptor,
                    descriptor,
                    part,
                    tuple(identities),
                )
            closing = descriptor
            descriptor = -1
            try:
                close_descriptor_once(
                    closing, _close, "replay directory close failed safely"
                )
            except ContractError:
                close_descriptor_once(
                    next_descriptor, _close, "replay directory close failed safely"
                )
                raise
            descriptor = next_descriptor
    except OSError as error:
        if descriptor >= 0:
            close_descriptor_once(
                descriptor, _close, "replay directory close failed safely"
            )
        raise ContractError(f"{label} must be an existing safe directory") from error
    raise ContractError(f"{label} must be an existing safe directory")


def verify_replay_directory(binding: ReplayDirectory, label: str) -> None:
    if _inode(os.fstat(binding.parent_descriptor)) != binding.component_identities[-2]:
        raise ContractError(f"{label} changed")
    try:
        leaf = os.stat(
            binding.leaf_name,
            dir_fd=binding.parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise ContractError(f"{label} changed") from error
    if _inode(leaf) != _inode(os.fstat(binding.descriptor)):
        raise ContractError(f"{label} changed")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        if _inode(os.fstat(descriptor)) != binding.component_identities[0]:
            raise ContractError(f"{label} changed")
        for part, expected in zip(
            binding.path.parts[1:], binding.component_identities[1:], strict=True
        ):
            next_descriptor = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            closing = descriptor
            descriptor = -1
            try:
                close_descriptor_once(
                    closing, _close, "replay directory close failed safely"
                )
            except ContractError:
                close_descriptor_once(
                    next_descriptor, _close, "replay directory close failed safely"
                )
                raise
            descriptor = next_descriptor
            if _inode(os.fstat(descriptor)) != expected:
                raise ContractError(f"{label} changed")
        if _inode(os.fstat(descriptor)) != _inode(os.fstat(binding.descriptor)):
            raise ContractError(f"{label} changed")
    except OSError as error:
        raise ContractError(f"{label} changed") from error
    finally:
        if descriptor >= 0:
            close_descriptor_once(
                descriptor, _close, "replay directory close failed safely"
            )


def close_replay_directory(binding: ReplayDirectory) -> None:
    first_error: ContractError | None = None
    for descriptor in (binding.descriptor, binding.parent_descriptor):
        try:
            close_descriptor_once(
                descriptor, _close, "replay directory close failed safely"
            )
        except ContractError as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


__all__ = [
    "ReplayDirectory",
    "close_replay_directory",
    "open_replay_directory",
    "verify_replay_directory",
]
