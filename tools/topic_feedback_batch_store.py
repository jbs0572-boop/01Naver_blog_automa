from __future__ import annotations

import fcntl
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, final

from tools.contract_types import ContractError
from tools.topic_feedback_models import TopicFeedbackArtifact
from tools.topic_feedback_store import (
    SnapshotLocation,
    StoredSnapshot,
    encode_snapshot,
    ensure_snapshot_writes_enabled,
)
from tools.topic_feedback_store_fs import (
    directory_chain_is_current,
    open_directory,
    open_root,
    read_file,
    unlink_if_present,
)

_CREATE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
type BatchFault = Callable[[int, str], None]


@dataclass(frozen=True, slots=True)
class BatchItem:
    location: SnapshotLocation
    artifact: TopicFeedbackArtifact


@dataclass(frozen=True, slots=True)
class _Prepared:
    location: SnapshotLocation
    encoded: bytes
    digest: str


@dataclass(frozen=True, slots=True)
class _Parent:
    descriptors: tuple[int, ...]

    @property
    def leaf(self) -> int:
        return self.descriptors[-1]


@dataclass(frozen=True, slots=True)
class _Staged:
    index: int
    parent: _Parent
    temporary: str
    prepared: _Prepared


@final
class TopicFeedbackBatchStore:
    def __init__(self, root: Path, fault: BatchFault | None = None) -> None:
        self._root = root
        self._fault = fault
        self._root_fd = open_root(root)

    def __enter__(self) -> TopicFeedbackBatchStore:
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

    def _prepare(self, item: BatchItem) -> _Prepared:
        payload = item.artifact.payload
        if payload.get("schema_version") != item.location.kind.value:
            raise ContractError("snapshot kind does not match its path")
        if payload.get("as_of_date") != item.location.as_of_date:
            raise ContractError("snapshot date does not match its path")
        encoded = encode_snapshot(item.artifact)
        digest = payload.get("digest")
        if not isinstance(digest, str):
            raise ContractError("snapshot digest is invalid")
        return _Prepared(item.location, encoded, digest)

    def _parent(self, location: SnapshotLocation) -> _Parent:
        descriptors: list[int] = []
        parent = self._root_fd
        try:
            for component in location.relative_path.parts[:-1]:
                parent = open_directory(parent, component, create=True)
                descriptors.append(parent)
        except ContractError:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise
        return _Parent(tuple(descriptors))

    def _current(self, location: SnapshotLocation, parent: _Parent) -> bool:
        return directory_chain_is_current(
            (self._root, self._root_fd),
            tuple(
                zip(
                    location.relative_path.parts[:-1],
                    parent.descriptors,
                    strict=True,
                )
            ),
        )

    @staticmethod
    def _absent(parent: _Parent, name: str) -> None:
        try:
            _ = os.stat(name, dir_fd=parent.leaf, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as error:
            raise ContractError("snapshot destination is unsafe") from error
        raise ContractError("snapshot is append-only")

    def _stage(self, index: int, parent: _Parent, prepared: _Prepared) -> _Staged:
        temporary = f".batch-{uuid.uuid4().hex}"
        created = False
        try:
            descriptor = os.open(temporary, _CREATE_FLAGS, 0o644, dir_fd=parent.leaf)
            created = True
            with os.fdopen(descriptor, "wb") as handle:
                _ = handle.write(prepared.encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if self._fault is not None:
                self._fault(index, "stage")
        except OSError as error:
            if created:
                unlink_if_present(parent.leaf, temporary)
            raise ContractError("batch staging failed safely") from error
        return _Staged(index, parent, temporary, prepared)

    def _publish(self, staged: _Staged) -> None:
        if self._fault is not None:
            self._fault(staged.index, "publish")
        os.link(
            staged.temporary,
            staged.prepared.location.relative_path.name,
            src_dir_fd=staged.parent.leaf,
            dst_dir_fd=staged.parent.leaf,
            follow_symlinks=False,
        )
        os.fsync(staged.parent.leaf)
        if self._fault is not None:
            self._fault(staged.index, "post_publish")

    @staticmethod
    def _rollback(staged: _Staged) -> None:
        name = staged.prepared.location.relative_path.name
        try:
            final = os.stat(name, dir_fd=staged.parent.leaf, follow_symlinks=False)
            temporary = os.stat(
                staged.temporary, dir_fd=staged.parent.leaf, follow_symlinks=False
            )
            if (final.st_dev, final.st_ino) != (temporary.st_dev, temporary.st_ino):
                return
            if read_file(staged.parent.leaf, name) != staged.prepared.encoded:
                return
            os.unlink(name, dir_fd=staged.parent.leaf)
            os.fsync(staged.parent.leaf)
        except (OSError, ContractError):
            return

    def store(self, items: tuple[BatchItem, ...]) -> tuple[StoredSnapshot, ...]:
        ensure_snapshot_writes_enabled(self._root)
        prepared = tuple(self._prepare(item) for item in items)
        targets = tuple(item.location.relative_path for item in prepared)
        if len(set(targets)) != len(targets):
            raise ContractError("snapshot batch contains duplicate destinations")
        parents: dict[Path, _Parent] = {}
        staged: list[_Staged] = []
        created: list[_Staged] = []
        try:
            for item in prepared:
                key = item.location.relative_path.parent
                if key not in parents:
                    parents[key] = self._parent(item.location)
            ordered = tuple(parents[key] for key in sorted(parents, key=str))
            for parent in ordered:
                fcntl.flock(parent.leaf, fcntl.LOCK_EX)
            for item in prepared:
                parent = parents[item.location.relative_path.parent]
                if not self._current(item.location, parent):
                    raise ContractError("snapshot store parent changed")
                self._absent(parent, item.location.relative_path.name)
            for index, item in enumerate(prepared):
                parent = parents[item.location.relative_path.parent]
                staged.append(self._stage(index, parent, item))
            for entry in staged:
                if not self._current(entry.prepared.location, entry.parent):
                    raise ContractError("snapshot store parent changed")
                self._publish(entry)
                created.append(entry)
            for entry in created:
                if not self._current(entry.prepared.location, entry.parent):
                    raise ContractError("snapshot store parent changed")
                if (
                    read_file(
                        entry.parent.leaf, entry.prepared.location.relative_path.name
                    )
                    != entry.prepared.encoded
                ):
                    raise ContractError("snapshot canonical bytes mismatch")
            return tuple(
                StoredSnapshot(
                    self._root / entry.prepared.location.relative_path,
                    entry.prepared.encoded,
                    entry.prepared.digest,
                )
                for entry in created
            )
        except OSError as error:
            for entry in reversed(staged):
                self._rollback(entry)
            raise ContractError("batch write failed safely") from error
        except ContractError:
            for entry in reversed(staged):
                self._rollback(entry)
            raise
        finally:
            for entry in staged:
                unlink_if_present(entry.parent.leaf, entry.temporary)
            for key in sorted(parents, key=str, reverse=True):
                parent = parents[key]
                try:
                    fcntl.flock(parent.leaf, fcntl.LOCK_UN)
                finally:
                    for descriptor in reversed(parent.descriptors):
                        os.close(descriptor)
