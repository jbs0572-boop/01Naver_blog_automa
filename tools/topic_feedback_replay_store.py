from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Final

from tools.contract_types import ContractError
from tools.topic_feedback_replay_binding import (
    ReplayDirectory,
    verify_replay_directory,
)
from tools.topic_feedback_replay_io import (
    close_descriptor_once,
    retry_sync,
    write_all,
)
from tools.topic_feedback_replay_verified import (
    VerifiedFile,
    locked_descriptor,
    open_verified_file,
    reverify_file,
    verify_descriptor,
    verify_file_path,
)

_CREATE_FLAGS: Final = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_OUTPUT_NAME: Final = "task-13-e2e.json"
_OBJECT_PREFIX: Final = ".replay-object-"


@dataclass(frozen=True, slots=True)
class Publication:
    binding: ReplayDirectory
    created: bool


def _publish_write(descriptor: int, encoded: memoryview) -> int:
    return os.write(descriptor, encoded)


def _publish_sync(descriptor: int) -> None:
    os.fsync(descriptor)


def _publish_close(descriptor: int) -> None:
    os.close(descriptor)


def _publish_link(parent_fd: int, object_name: str) -> None:
    os.link(
        object_name,
        _OUTPUT_NAME,
        src_dir_fd=parent_fd,
        dst_dir_fd=parent_fd,
        follow_symlinks=False,
    )


def _create_object(parent_fd: int, object_name: str) -> int | None:
    try:
        return os.open(object_name, _CREATE_FLAGS, 0o600, dir_fd=parent_fd)
    except FileExistsError:
        return None
    except OSError as error:
        raise ContractError("replay content object creation failed safely") from error


def _open_object(
    parent_fd: int, object_name: str, encoded: bytes
) -> tuple[VerifiedFile, bool]:
    descriptor = _create_object(parent_fd, object_name)
    if descriptor is None:
        return (
            open_verified_file(
                parent_fd,
                object_name,
                encoded,
                "replay content object",
                _publish_close,
            ),
            False,
        )
    try:
        write_all(descriptor, encoded, _publish_write)
        retry_sync(descriptor, _publish_sync, "replay evidence sync failed safely")
        return verify_descriptor(descriptor, encoded, "replay content object"), True
    except ContractError:
        close_descriptor_once(
            descriptor, _publish_close, "replay evidence close failed safely"
        )
        raise


def _verify_link_count(file: VerifiedFile, expected: int, label: str) -> None:
    try:
        links = os.fstat(file.descriptor).st_nlink
    except OSError as error:
        raise ContractError(f"{label} changed") from error
    if links != expected:
        raise ContractError(f"{label} collision")


def _verify_final_pair(payload: VerifiedFile, output: VerifiedFile) -> None:
    try:
        payload_stat = os.fstat(payload.descriptor)
        output_stat = os.fstat(output.descriptor)
    except OSError as error:
        raise ContractError("replay content object changed") from error
    payload_state = (
        payload_stat.st_dev,
        payload_stat.st_ino,
        payload_stat.st_size,
        payload_stat.st_nlink,
    )
    output_state = (
        output_stat.st_dev,
        output_stat.st_ino,
        output_stat.st_size,
        output_stat.st_nlink,
    )
    if payload_state != output_state or payload_stat.st_nlink != 2:
        raise ContractError("replay content object collision")


def rollback_publication(publication: Publication) -> None:
    del publication


def _publish_locked(binding: ReplayDirectory, encoded: bytes) -> Publication:
    parent_fd = binding.descriptor
    object_name = f"{_OBJECT_PREFIX}{hashlib.sha256(encoded).hexdigest()}.json"
    payload, object_created = _open_object(parent_fd, object_name, encoded)
    try:
        _verify_link_count(payload, 1 if object_created else 2, "replay content object")
        verify_file_path(parent_fd, object_name, payload, "replay content object")
        if object_created:
            try:
                _publish_link(parent_fd, object_name)
            except FileExistsError:
                raise ContractError("replay evidence output collision") from None
            except OSError as error:
                raise ContractError("replay evidence link failed safely") from error
        try:
            output = open_verified_file(
                parent_fd,
                _OUTPUT_NAME,
                encoded,
                "replay evidence output",
                _publish_close,
            )
        except ContractError as error:
            if not object_created:
                raise ContractError("replay content object collision") from error
            raise
        try:
            if output.identity != payload.identity:
                raise ContractError("replay evidence output changed")
            _verify_link_count(payload, 2, "replay content object")
            verify_replay_directory(binding, "evidence directory")
            verify_file_path(parent_fd, object_name, payload, "replay content object")
            verify_file_path(parent_fd, _OUTPUT_NAME, output, "replay evidence output")
            retry_sync(
                parent_fd,
                _publish_sync,
                "replay evidence directory sync failed safely",
            )
            verify_replay_directory(binding, "evidence directory")
            verify_file_path(parent_fd, object_name, payload, "replay content object")
            verify_file_path(parent_fd, _OUTPUT_NAME, output, "replay evidence output")
            reverify_file(payload, encoded, "replay content object")
            reverify_file(output, encoded, "replay evidence output")
            _verify_final_pair(payload, output)
            return Publication(binding, object_created)
        finally:
            close_descriptor_once(
                output.descriptor,
                _publish_close,
                "replay evidence close failed safely",
            )
    finally:
        close_descriptor_once(
            payload.descriptor, _publish_close, "replay evidence close failed safely"
        )


def publish_evidence(binding: ReplayDirectory, encoded: bytes) -> Publication:
    with locked_descriptor(binding.descriptor):
        verify_replay_directory(binding, "evidence directory")
        return _publish_locked(binding, encoded)


__all__ = [
    "Publication",
    "publish_evidence",
    "rollback_publication",
    "verify_replay_directory",
]
