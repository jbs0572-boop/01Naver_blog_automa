from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from tools.contract_types import ContractError, JSONMap, JSONValue

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_CREATE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW
_RECORD_FIELDS: Final = frozenset(["schema_version", "rollback_id", "captured_at", "source_config_digest", "feedback_enabled", "active_score_version", "shadow_score_version", "selection_mutation", "new_feedback_artifacts_enabled", "digest"])  # fmt: skip


@dataclass(frozen=True, slots=True)
class RollbackFence:
    registry_digest: str
    record_digests: tuple[str, ...]


def _safe_id(rollback_id: str) -> str:
    path = Path(rollback_id)
    if (
        not rollback_id
        or rollback_id.startswith(".")
        or path.is_absolute()
        or len(path.parts) != 1
    ):
        raise ContractError("rollback_id must be a safe path component")
    return rollback_id


def _open_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o755, dir_fd=parent_fd)
    except FileExistsError:
        existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(existing.st_mode):
            raise ContractError("rollback registry path is unsafe") from None
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise ContractError("rollback registry path is unsafe") from error


def _read(parent_fd: int, name: str) -> bytes:
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise ContractError("rollback registry record is unsafe") from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ContractError("rollback registry record is unsafe")
    try:
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()
    except OSError as error:
        raise ContractError("rollback registry record is unsafe") from error


def _canonical(value: JSONMap) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ContractError("rollback registry record is invalid") from error


def _sha256(encoded: bytes) -> str:
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _parse_record(name: str, encoded: bytes) -> str:
    if not name.endswith(".json") or name.startswith("."):
        raise ContractError("rollback registry record is invalid")
    try:
        value: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("rollback registry record is invalid") from error
    if not isinstance(value, dict) or frozenset(value) != _RECORD_FIELDS:
        raise ContractError("rollback registry record is invalid")
    rollback_id = value.get("rollback_id")
    captured_at = value.get("captured_at")
    source_digest = value.get("source_config_digest")
    digest = value.get("digest")
    if (
        not isinstance(rollback_id, str)
        or name != _safe_id(rollback_id) + ".json"
        or not isinstance(captured_at, str)
        or not isinstance(source_digest, str)
        or not isinstance(digest, str)
        or len(source_digest) != 71
        or not source_digest.startswith("sha256:")
        or len(digest) != 71
        or not digest.startswith("sha256:")
    ):
        raise ContractError("rollback registry record is invalid")
    try:
        parsed = datetime.fromisoformat(captured_at)
    except ValueError as error:
        raise ContractError("rollback registry record is invalid") from error
    if (
        parsed.utcoffset() is None
        or not captured_at.endswith("+09:00")
        or value.get("schema_version") != "topic-feedback-rollback-v1"
        or value.get("feedback_enabled") is not False
        or value.get("active_score_version") != "topic-baseline-v1"
        or value.get("shadow_score_version") != "topic-feedback-v1"
        or value.get("selection_mutation") is not False
        or value.get("new_feedback_artifacts_enabled") is not False
    ):
        raise ContractError("rollback registry record is invalid")
    unsigned: JSONMap = {key: item for key, item in value.items() if key != "digest"}
    if digest != _sha256(_canonical(unsigned)) or encoded != _canonical(value) + b"\n":
        raise ContractError("rollback registry record digest is invalid")
    return digest


def _existing_directory(parent_fd: int, name: str) -> int | None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(metadata.st_mode):
        raise ContractError("rollback registry path is unsafe")
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise ContractError("rollback registry path is unsafe") from error


def load_rollback_fence(root: Path) -> RollbackFence | None:
    if root.is_symlink():
        raise ContractError("rollback root must not be a symlink")
    try:
        root_fd = os.open(root, _DIRECTORY_FLAGS)
    except OSError as error:
        raise ContractError("rollback root must be an existing directory") from error
    metadata_fd: int | None = None
    registry_fd: int | None = None
    try:
        metadata_fd = _existing_directory(root_fd, "metadata")
        if metadata_fd is None:
            return None
        registry_fd = _existing_directory(metadata_fd, "topic-feedback-rollbacks")
        if registry_fd is None:
            return None
        digests = tuple(
            _parse_record(name, _read(registry_fd, name))
            for name in sorted(os.listdir(registry_fd))
        )
    finally:
        for descriptor in (registry_fd, metadata_fd, root_fd):
            if descriptor is not None:
                os.close(descriptor)
    if not digests:
        return None
    return RollbackFence(_sha256("\n".join(digests).encode("ascii")), digests)


def _publish(parent_fd: int, name: str, encoded: bytes) -> None:
    temporary = f".tmp-{uuid.uuid4().hex}"
    temporary_created = False
    try:
        descriptor = os.open(temporary, _CREATE_FLAGS, 0o644, dir_fd=parent_fd)
        temporary_created = True
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
            if _read(parent_fd, name) != encoded:
                raise ContractError("rollback identity collision") from None
    except OSError as error:
        raise ContractError("rollback registry write failed safely") from error
    finally:
        if temporary_created:
            os.unlink(temporary, dir_fd=parent_fd)


def store_rollback(root: Path, rollback_id: str, encoded: bytes) -> Path:
    name = _safe_id(rollback_id) + ".json"
    if root.is_symlink():
        raise ContractError("rollback root must not be a symlink")
    try:
        root_fd = os.open(root, _DIRECTORY_FLAGS)
    except OSError as error:
        raise ContractError("rollback root must be an existing directory") from error
    metadata_fd: int | None = None
    registry_fd: int | None = None
    try:
        metadata_fd = _open_directory(root_fd, "metadata")
        registry_fd = _open_directory(metadata_fd, "topic-feedback-rollbacks")
        _publish(registry_fd, name, encoded)
    finally:
        for descriptor in (registry_fd, metadata_fd, root_fd):
            if descriptor is not None:
                os.close(descriptor)
    return root / "metadata" / "topic-feedback-rollbacks" / name
