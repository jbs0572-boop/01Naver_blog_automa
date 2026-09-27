from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import NotionWriteOperation
from tools.notion_api_http import NotionHttp, text
from tools.notion_api_pages import (
    append_page,
    create_page,
    find_pages,
    next_sequence,
    target_schema,
)
from tools.notion_api_payload import canonical_block
from tools.notion_api_roundtrip import (
    children,
    fetch_page,
)
from tools.notion_api_roundtrip import (
    verify_page_parent as verify_remote_page_parent,
)
from tools.notion_api_uploads import (
    complete_upload,
    create_upload,
    find_uploads,
    initialize_upload,
)
from tools.notion_content import AssetMetadata, ParsedNotionCopy, notion_children
from tools.notion_transport import AttachmentSpec


@dataclass(frozen=True, slots=True)
class NotionApiTransport:
    client: httpx2.Client
    root_blocks: tuple[JSONMap, ...] = ()
    page_properties: JSONMap = field(default_factory=dict)
    parsed: ParsedNotionCopy | None = None
    target_id: str | None = None
    uploaded_ids_by_filename: dict[str, str] = field(default_factory=dict, repr=False)
    enforce_schema: bool = False
    asset_metadata: tuple[AssetMetadata, ...] = ()
    image_filenames: tuple[str, ...] = ()
    http: NotionHttp | None = None
    write_authorizer: Callable[[NotionWriteOperation], None] | None = field(
        default=None, repr=False
    )

    @property
    def requests(self) -> NotionHttp:
        return self.http or NotionHttp(self.client)

    def find_attachments(
        self, target_id: str, name: str, *, timeout_seconds: float
    ) -> tuple[JSONMap, ...]:
        return find_uploads(self.requests, target_id, name, timeout_seconds)

    def create_attachment(
        self, target_id: str, spec: AttachmentSpec, *, timeout_seconds: float
    ) -> JSONMap:
        validator: Callable[[str], None] | None = None
        if self.enforce_schema:

            def validate(value: str) -> None:
                self.validate_schema(value, timeout_seconds=timeout_seconds)

            validator = validate
        return create_upload(
            self.client,
            self.requests,
            target_id,
            spec,
            timeout_seconds,
            validator,
            self._authorization("create_attachment"),
            self.uploaded_ids_by_filename,
        )

    def initialize_attachment(
        self, target_id: str, spec: AttachmentSpec, *, timeout_seconds: float
    ) -> JSONMap:
        validator: Callable[[str], None] | None = None
        if self.enforce_schema:

            def validate(value: str) -> None:
                self.validate_schema(value, timeout_seconds=timeout_seconds)

            validator = validate
        return initialize_upload(
            self.requests,
            target_id,
            spec,
            timeout_seconds,
            validator,
            self._authorization("create_attachment"),
        )

    def complete_attachment(
        self,
        target_id: str,
        upload_id: str,
        spec: AttachmentSpec,
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        validator: Callable[[str], None] | None = None
        if self.enforce_schema:

            def validate(value: str) -> None:
                self.validate_schema(value, timeout_seconds=timeout_seconds)

            validator = validate
        return complete_upload(
            self.client,
            self.requests,
            target_id,
            upload_id,
            spec,
            timeout_seconds,
            validator,
            self._authorization("create_attachment"),
            self.uploaded_ids_by_filename,
        )

    def find_pages(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        *,
        timeout_seconds: float,
    ) -> tuple[JSONMap, ...]:
        return find_pages(
            self.requests, target_id, run_id, artifact_digest, timeout_seconds
        )

    def create_page(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        attachment_ids: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        self.bind_attachment_ids(attachment_ids)
        created = create_page(
            self.requests,
            target_id,
            self.page_properties,
            self._roots(),
            timeout_seconds,
            self._page_create_authorization(target_id, timeout_seconds),
        )
        return {
            "id": text(created, "id"),
            "target_id": target_id,
            "run_id": run_id,
            "artifact_digest": artifact_digest,
        }

    def bind_attachment_ids(self, attachment_ids: tuple[str, ...]) -> None:
        if len(attachment_ids) != len(self.image_filenames):
            raise ContractError("Notion attachment count does not match image template")
        self.uploaded_ids_by_filename.update(
            zip(self.image_filenames, attachment_ids, strict=True)
        )

    def page_root_count(self) -> int:
        return len(self._roots())

    def verify_page_parent(
        self, page_id: str, target_id: str, *, timeout_seconds: float
    ) -> None:
        if not target_id:
            raise ContractError("Notion append target identity is missing")
        configured_target = self.target_id
        if configured_target is not None and configured_target != target_id:
            raise ContractError("Notion append target identity does not match plan")
        if self.enforce_schema:
            self.validate_schema(target_id, timeout_seconds=timeout_seconds)
        page = self.requests.read("GET", f"/v1/pages/{page_id}", timeout_seconds)
        verify_remote_page_parent(page, target_id)

    def append_page(
        self, page_id: str, start_index: int, *, timeout_seconds: float
    ) -> JSONMap:
        return append_page(
            self.requests,
            page_id,
            self._roots(),
            start_index,
            timeout_seconds,
            self._append_authorization(page_id, timeout_seconds),
        )

    def verified_root_count(self, page_id: str, *, timeout_seconds: float) -> int:
        actual = children(self.requests, page_id, timeout_seconds)
        expected = tuple(canonical_block(value) for value in self._roots())
        if tuple(canonical_block(value) for value in actual) != expected[: len(actual)]:
            raise ContractError("Notion page root prefix does not match this write")
        return len(actual)

    def fetch_page(self, page_id: str, *, timeout_seconds: float) -> JSONMap:
        return fetch_page(
            self.client,
            self.requests,
            page_id,
            timeout_seconds,
            self.page_properties,
            self.asset_metadata,
            self.target_id,
        )

    def validate_schema(self, target_id: str, *, timeout_seconds: float) -> None:
        _ = target_schema(self.requests, target_id, timeout_seconds)

    def next_sequence(
        self, target_id: str, execution_date: str, *, timeout_seconds: float
    ) -> int:
        return next_sequence(self.requests, target_id, execution_date, timeout_seconds)

    def _roots(self) -> tuple[JSONMap, ...]:
        if self.parsed is None:
            return self.root_blocks
        return tuple(notion_children(self.parsed, self.uploaded_ids_by_filename))

    def _authorization(
        self, operation: NotionWriteOperation
    ) -> Callable[[], None] | None:
        authorizer = self.write_authorizer
        if authorizer is None:
            return None
        return lambda: authorizer(operation)

    def _page_create_authorization(
        self, target_id: str, timeout_seconds: float
    ) -> Callable[[], None] | None:
        external = self._authorization("create_page")
        if not self.enforce_schema:
            return external

        def authorize() -> None:
            self.validate_schema(target_id, timeout_seconds=timeout_seconds)
            if external is not None:
                external()

        return authorize

    def _append_authorization(
        self, page_id: str, timeout_seconds: float
    ) -> Callable[[], None]:
        target_id = self.target_id
        if target_id is None:
            raise ContractError("Notion append target identity is missing")
        external = self._authorization("create_page")

        def authorize() -> None:
            if self.enforce_schema:
                self.validate_schema(target_id, timeout_seconds=timeout_seconds)
            page = self.requests.read("GET", f"/v1/pages/{page_id}", timeout_seconds)
            verify_remote_page_parent(page, target_id)
            if external is not None:
                external()

        return authorize
