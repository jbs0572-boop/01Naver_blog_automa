from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Final, final

from tools.contract_types import ContractError, JSONValue
from tools.topic_feedback_models import (
    ArtifactKind,
    TopicFeedbackArtifact,
    parse_artifact,
    serialize_artifact,
)
from tools.topic_feedback_rollback_store import load_rollback_fence
from tools.topic_feedback_store_fs import (
    directory_chain_is_current,
    open_directory,
    open_root,
    publish_exclusive,
    read_file,
)

_KIND_DIRECTORIES: Final = {
    ArtifactKind.TOPIC_SIGNAL: "topic-signals",
    ArtifactKind.BLOG_STAT: "blog-stats",
    ArtifactKind.BLOG_STAT_V2: "blog-stats",
    ArtifactKind.PUBLICATION_LINK: "publication-links",
    ArtifactKind.WEEKLY_FEEDBACK: "weekly-feedback",
}


@dataclass(frozen=True, slots=True)
class SnapshotLocation:
    kind: ArtifactKind
    owner_id: str
    as_of_date: str
    artifact_id: str

    def __post_init__(self) -> None:
        _ = _safe_component(self.owner_id)
        _ = _safe_date(self.as_of_date)
        _ = _safe_component(self.artifact_id)

    @classmethod
    def topic_signal(
        cls, source_id: str, as_of_date: str, capture_id: str
    ) -> SnapshotLocation:
        return cls(
            ArtifactKind.TOPIC_SIGNAL,
            _safe_component(source_id),
            _safe_date(as_of_date),
            _safe_component(capture_id),
        )

    @classmethod
    def blog_stat(
        cls, blog_id: str, as_of_date: str, capture_id: str
    ) -> SnapshotLocation:
        return cls(
            ArtifactKind.BLOG_STAT_V2,
            _safe_component(blog_id),
            _safe_date(as_of_date),
            _safe_component(capture_id),
        )

    @property
    def relative_path(self) -> Path:
        return (
            Path("metadata")
            / _kind_directory(self.kind)
            / self.owner_id
            / self.as_of_date
            / f"{self.artifact_id}.json"
        )


@dataclass(frozen=True, slots=True)
class StoredSnapshot:
    path: Path
    encoded: bytes
    digest: str


def _safe_component(value: str) -> str:
    path = Path(value)
    if (
        not value
        or value.startswith(".")
        or path.is_absolute()
        or len(path.parts) != 1
        or path.name != value
    ):
        raise ContractError("snapshot path escapes root")
    return value


def _safe_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ContractError("snapshot path escapes root") from error
    if parsed.isoformat() != value:
        raise ContractError("snapshot path escapes root")
    return value


def _kind_directory(kind: ArtifactKind) -> str:
    return _KIND_DIRECTORIES[kind]


def encode_snapshot(artifact: TopicFeedbackArtifact) -> bytes:
    return serialize_artifact(artifact).encode("utf-8") + b"\n"


def ensure_snapshot_writes_enabled(root: Path) -> None:
    if load_rollback_fence(root) is not None:
        raise ContractError("new feedback artifacts are disabled by rollback")


def _decode(encoded: bytes) -> TopicFeedbackArtifact:
    try:
        value: JSONValue = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("snapshot is invalid") from error
    if not isinstance(value, dict):
        raise ContractError("snapshot is invalid")
    return parse_artifact(value)


@final
class TopicFeedbackStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root_fd = open_root(root)

    def __enter__(self) -> TopicFeedbackStore:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        descriptor = getattr(self, "_root_fd", None)
        if isinstance(descriptor, int):
            os.close(descriptor)
            del self._root_fd

    def _open_parent(self, location: SnapshotLocation, *, create: bool) -> tuple[int, ...]:
        descriptors: list[int] = []
        parent_fd = self._root_fd
        try:
            for component in location.relative_path.parts[:-1]:
                descriptor = open_directory(parent_fd, component, create=create)
                descriptors.append(descriptor)
                parent_fd = descriptor
        except ContractError:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise
        return tuple(descriptors)

    def _path_is_current(
        self, location: SnapshotLocation, descriptors: tuple[int, ...]
    ) -> bool:
        return directory_chain_is_current(
            (self._root, self._root_fd),
            tuple(zip(location.relative_path.parts[:-1], descriptors, strict=True)),
        )

    def store(
        self, location: SnapshotLocation, artifact: TopicFeedbackArtifact
    ) -> StoredSnapshot:
        ensure_snapshot_writes_enabled(self._root)
        schema_version = artifact.payload.get("schema_version")
        if schema_version != location.kind.value:
            raise ContractError("snapshot kind does not match its path")
        if artifact.payload.get("as_of_date") != location.as_of_date:
            raise ContractError("snapshot date does not match its path")
        encoded = encode_snapshot(artifact)
        descriptors = self._open_parent(location, create=True)
        parent_fd = descriptors[-1]
        try:
            fcntl.flock(parent_fd, fcntl.LOCK_EX)
            try:
                if not self._path_is_current(location, descriptors):
                    raise ContractError("snapshot store parent changed")
                publish_exclusive(parent_fd, location.relative_path.name, encoded)
                if not self._path_is_current(location, descriptors):
                    raise ContractError("snapshot store parent changed")
            finally:
                fcntl.flock(parent_fd, fcntl.LOCK_UN)
            stored = self._stored(
                location, read_file(parent_fd, location.relative_path.name)
            )
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        return stored

    def read_strict(self, location: SnapshotLocation) -> StoredSnapshot:
        descriptors = self._open_parent(location, create=False)
        try:
            if not self._path_is_current(location, descriptors):
                raise ContractError("snapshot store parent changed")
            return self._stored(
                location,
                read_file(descriptors[-1], location.relative_path.name),
            )
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _stored(self, location: SnapshotLocation, encoded: bytes) -> StoredSnapshot:
        artifact = _decode(encoded)
        if artifact.payload.get("schema_version") != location.kind.value:
            raise ContractError("snapshot kind does not match its path")
        if artifact.payload.get("as_of_date") != location.as_of_date:
            raise ContractError("snapshot date does not match its path")
        digest = artifact.payload.get("digest")
        if not isinstance(digest, str):
            raise ContractError("snapshot digest is invalid")
        if encoded != encode_snapshot(artifact):
            raise ContractError("snapshot canonical bytes mismatch")
        return StoredSnapshot(self._root / location.relative_path, encoded, digest)
