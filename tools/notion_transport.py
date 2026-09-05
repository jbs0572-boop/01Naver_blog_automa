from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, override

from tools.contract_types import JSONMap
from tools.manifest import ManifestFile


@dataclass(frozen=True, slots=True)
class AttachmentSpec:
    path: Path
    name: str
    role: str
    sha256: str
    order: int


@dataclass(slots=True)
class NotionCreateUncertain(Exception):
    resource: str

    @override
    def __str__(self) -> str:
        return f"Notion {self.resource} create outcome is uncertain"


class NotionTransport(Protocol):
    def find_attachments(
        self, target_id: str, name: str, *, timeout_seconds: float
    ) -> Sequence[JSONMap]: ...

    def create_attachment(
        self, target_id: str, spec: AttachmentSpec, *, timeout_seconds: float
    ) -> JSONMap: ...

    def initialize_attachment(
        self, target_id: str, spec: AttachmentSpec, *, timeout_seconds: float
    ) -> JSONMap: ...

    def complete_attachment(
        self,
        target_id: str,
        upload_id: str,
        spec: AttachmentSpec,
        *,
        timeout_seconds: float,
    ) -> JSONMap: ...

    def find_pages(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        *,
        timeout_seconds: float,
    ) -> Sequence[JSONMap]: ...

    def create_page(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        attachment_ids: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> JSONMap: ...

    def fetch_page(self, page_id: str, *, timeout_seconds: float) -> JSONMap: ...

    def verify_page_parent(
        self, page_id: str, target_id: str, *, timeout_seconds: float
    ) -> None: ...

    def page_root_count(self) -> int: ...

    def verified_root_count(self, page_id: str, *, timeout_seconds: float) -> int: ...

    def append_page(
        self, page_id: str, start_index: int, *, timeout_seconds: float
    ) -> JSONMap: ...


def image_entries(files: tuple[ManifestFile, ...]) -> tuple[ManifestFile, ...]:
    images = (entry for entry in files if entry.role in {"body_image", "thumbnail"})
    return tuple(
        sorted(images, key=lambda entry: (entry.role != "thumbnail", entry.order))
    )


__all__ = [
    "AttachmentSpec",
    "NotionCreateUncertain",
    "NotionTransport",
    "image_entries",
]
