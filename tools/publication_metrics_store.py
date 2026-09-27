from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.topic_feedback_models import parse_artifact

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW
_CREATE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


@dataclass(frozen=True, slots=True)
class PublicationCommit:
    run_id: str
    blog_post_id: str | None
    encoded: bytes


@dataclass(frozen=True, slots=True)
class PublicationStored:
    path: Path
    encoded: bytes


def _safe_name(value: str, label: str) -> str:
    path = Path(value)
    if not value or value.startswith(".") or path.is_absolute() or len(path.parts) != 1:
        raise ContractError(f"{label} must be a single safe path component")
    return value


def _open_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o755, dir_fd=parent_fd)
    except FileExistsError:
        existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(existing.st_mode):
            raise ContractError(
                "publication attribution store entry is a symlink or invalid directory"
            ) from None
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise ContractError("publication attribution store contains a symlink or invalid directory") from error


def _read_file(parent_fd: int, name: str) -> bytes:
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise ContractError("publication attribution file is unreadable or unsafe") from error
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def _publish_exclusive(
    parent_fd: int,
    name: str,
    encoded: bytes,
    fault: Callable[[str], None] | None,
) -> bool:
    temporary = f".tmp-{uuid.uuid4().hex}"
    try:
        descriptor = os.open(temporary, _CREATE_FLAGS, 0o644, dir_fd=parent_fd)
    except OSError as error:
        raise ContractError("publication attribution staging failed safely") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            midpoint = max(1, len(encoded) // 2)
            _ = handle.write(encoded[:midpoint])
            if fault is not None:
                fault("write")
            _ = handle.write(encoded[midpoint:])
            handle.flush()
            if fault is not None:
                fault("flush")
            os.fsync(handle.fileno())
            if fault is not None:
                fault("fsync")
        return _link_exclusive(parent_fd, temporary, parent_fd, name)
    except OSError as error:
        raise ContractError("publication attribution staging failed safely") from error
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _link_exclusive(source_fd: int, source: str, target_fd: int, target: str) -> bool:
    try:
        os.link(
            source,
            target,
            src_dir_fd=source_fd,
            dst_dir_fd=target_fd,
            follow_symlinks=False,
        )
        os.fsync(target_fd)
    except FileExistsError:
        return False
    except OSError as error:
        raise ContractError("publication attribution requires safe hard links") from error
    return True


def _json_map(encoded: bytes) -> JSONMap:
    try:
        value: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("publication attribution file is invalid") from error
    if not isinstance(value, dict):
        raise ContractError("publication attribution file is invalid")
    return value


def validate_publication_run_id(run_id: str) -> str:
    if run_id.startswith("."):
        raise ContractError("run_id uses a reserved internal namespace")
    return _safe_name(run_id, "run_id")


def _publication_payload(encoded: bytes) -> JSONMap:
    artifact = parse_artifact(_json_map(encoded))
    if artifact.payload.get("schema_version") != "publication-link-v1":
        raise ContractError("publication attribution reservation is invalid")
    return artifact.payload


def _logical_payload(encoded: bytes) -> JSONMap:
    logical = dict(_publication_payload(encoded))
    _ = logical.pop("captured_at", None)
    _ = logical.pop("digest", None)
    return logical


@final
class PublicationMetricsStore:
    def __init__(
        self,
        root: Path,
        after_reservation: Callable[[], None] | None = None,
        staged_fault: Callable[[str], None] | None = None,
    ) -> None:
        self._root = root
        self._after_reservation = after_reservation
        self._staged_fault = staged_fault
        try:
            self._root_fd = os.open(root, _DIRECTORY_FLAGS)
            self._metadata_fd = _open_directory(self._root_fd, "metadata")
            self._base_fd = _open_directory(self._metadata_fd, "publication-links")
            self._identity_fd = _open_directory(self._base_fd, ".identities")
        except ContractError:
            self.close()
            raise
        except OSError as error:
            self.close()
            raise ContractError("publication attribution store is unsafe") from error

    def __enter__(self) -> PublicationMetricsStore:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        for name in ("_identity_fd", "_base_fd", "_metadata_fd", "_root_fd"):
            descriptor = getattr(self, name, None)
            if isinstance(descriptor, int):
                os.close(descriptor)
                delattr(self, name)

    def _parent_is_current(self) -> bool:
        try:
            current = os.stat(
                "publication-links",
                dir_fd=self._metadata_fd,
                follow_symlinks=False,
            )
        except OSError:
            return False
        return stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == (
            os.fstat(self._base_fd).st_dev,
            os.fstat(self._base_fd).st_ino,
        )

    def _existing_collision(self, run_id: str, post_id: str) -> bool:
        for directory_name in sorted(os.listdir(self._base_fd)):
            if directory_name.startswith("."):
                continue
            try:
                directory_fd = os.open(
                    directory_name,
                    _DIRECTORY_FLAGS,
                    dir_fd=self._base_fd,
                )
            except OSError as error:
                raise ContractError("publication attribution store contains an unsafe entry") from error
            try:
                for filename in sorted(os.listdir(directory_fd)):
                    if not filename.endswith(".json"):
                        continue
                    artifact = parse_artifact(_json_map(_read_file(directory_fd, filename)))
                    payload = artifact.payload
                    if payload.get("blog_post_id") == post_id and payload.get("run_id") != run_id:
                        return True
            finally:
                os.close(directory_fd)
        return False

    def _reserve(self, request: PublicationCommit) -> tuple[bytes, str | None]:
        if request.blog_post_id is None:
            return request.encoded, None
        name = hashlib.sha256(request.blog_post_id.encode("utf-8")).hexdigest() + ".json"
        if _publish_exclusive(
            self._identity_fd,
            name,
            request.encoded,
            self._staged_fault,
        ):
            if self._after_reservation is not None:
                self._after_reservation()
            return request.encoded, name
        existing = _read_file(self._identity_fd, name)
        payload = _publication_payload(existing)
        if payload.get("blog_post_id") != request.blog_post_id or payload.get(
            "run_id"
        ) != request.run_id:
            raise ContractError("blog post identity collision")
        if _logical_payload(existing) != _logical_payload(request.encoded):
            raise ContractError("publication attribution replay diverges from reserved payload")
        return existing, name

    def commit(self, request: PublicationCommit) -> PublicationStored:
        run_id = validate_publication_run_id(request.run_id)
        fcntl.flock(self._base_fd, fcntl.LOCK_EX)
        try:
            if not self._parent_is_current():
                raise ContractError("publication attribution store parent changed")
            if request.blog_post_id is not None and self._existing_collision(
                run_id, request.blog_post_id
            ):
                raise ContractError("blog post identity collision")
            encoded, reservation = self._reserve(request)
            payload = _publication_payload(encoded)
            digest = payload.get("digest")
            if not isinstance(digest, str):
                raise ContractError("publication attribution reservation is invalid")
            filename = _safe_name(f"{digest.removeprefix('sha256:')[:24]}.json", "link_id")
            if not self._parent_is_current():
                raise ContractError("publication attribution store parent changed")
            run_fd = _open_directory(self._base_fd, run_id)
            try:
                created = (
                    _publish_exclusive(run_fd, filename, encoded, self._staged_fault)
                    if reservation is None
                    else _link_exclusive(self._identity_fd, reservation, run_fd, filename)
                )
                if not created and _read_file(run_fd, filename) != encoded:
                    raise ContractError("publication attribution is append-only")
            finally:
                os.close(run_fd)
            if not self._parent_is_current():
                raise ContractError("publication attribution store parent changed")
        finally:
            fcntl.flock(self._base_fd, fcntl.LOCK_UN)
        path = self._root / "metadata" / "publication-links" / run_id / filename
        return PublicationStored(path, encoded)
