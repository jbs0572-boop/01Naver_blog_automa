from __future__ import annotations

from tools.contract_types import ContractError, JSONMap
from tools.notion_resume import AttachmentSpec, NotionCreateUncertain

__all__ = ("MemoryTransport", "_page")


class MemoryTransport:
    def __init__(self) -> None:
        self.attachments: dict[str, list[JSONMap]] = {}
        self.pages: list[JSONMap] = []
        self.created_attachments: list[str] = []
        self.attachment_create_attempts: int = 0
        self.attachment_complete_attempts: int = 0
        self.attachment_initialize_attempts: int = 0
        self.uncertain_attachment_create: bool = False
        self.uncertain_attachment_pending: bool = False
        self.persist_uncertain_attachment: bool = True
        self.create_page_calls: int = 0
        self.uncertain_page_create: bool = False
        self.actual_page: JSONMap = _page("topic")
        self.transport_calls: int = 0
        self.timeouts: list[float] = []
        self.root_blocks: list[JSONMap] = []
        self.remote_roots: list[JSONMap] = []
        self.page_parent: JSONMap | None = {
            "type": "data_source_id",
            "data_source_id": "datasource-resume",
        }
        self.append_attempts: int = 0
        self.append_events: list[str] = []
        self.duplicate_after_create: bool = False

    def _record_timeout(self, timeout_seconds: float) -> None:
        self.transport_calls += 1
        self.timeouts.append(timeout_seconds)

    def find_attachments(
        self, target_id: str, name: str, *, timeout_seconds: float
    ) -> list[JSONMap]:
        _ = target_id
        self._record_timeout(timeout_seconds)
        return list(self.attachments.get(name, []))

    def create_attachment(
        self, target_id: str, spec: AttachmentSpec, *, timeout_seconds: float
    ) -> JSONMap:
        _ = target_id
        self._record_timeout(timeout_seconds)
        self.attachment_create_attempts += 1
        self.created_attachments.append(spec.name)
        attachment: JSONMap = {
            "id": f"attachment-{len(self.created_attachments)}",
            "status": "pending" if self.uncertain_attachment_pending else "uploaded",
        }
        if self.persist_uncertain_attachment or not self.uncertain_attachment_create:
            self.attachments.setdefault(spec.name, []).append(attachment)
        if self.uncertain_attachment_create or self.uncertain_attachment_pending:
            raise NotionCreateUncertain(resource="attachment")
        return attachment

    def initialize_attachment(
        self, target_id: str, spec: AttachmentSpec, *, timeout_seconds: float
    ) -> JSONMap:
        _ = target_id
        self._record_timeout(timeout_seconds)
        self.attachment_initialize_attempts += 1
        self.created_attachments.append(spec.name)
        attachment: JSONMap = {
            "id": f"attachment-{len(self.created_attachments)}",
            "status": "pending",
        }
        if self.persist_uncertain_attachment or not self.uncertain_attachment_create:
            self.attachments.setdefault(spec.name, []).append(attachment)
        if self.uncertain_attachment_create:
            raise NotionCreateUncertain(resource="attachment")
        return attachment

    def complete_attachment(
        self,
        target_id: str,
        upload_id: str,
        spec: AttachmentSpec,
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        _ = target_id
        self._record_timeout(timeout_seconds)
        self.attachment_complete_attempts += 1
        for attachments in self.attachments.values():
            for attachment in attachments:
                if attachment.get("id") == upload_id:
                    attachment.update(
                        status="uploaded",
                        filename=spec.name,
                        content_type="image/png",
                        content_length=spec.path.stat().st_size,
                    )
                    return attachment
        raise ContractError("pending attachment does not exist")

    def find_pages(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        *,
        timeout_seconds: float,
    ) -> list[JSONMap]:
        _ = (target_id, artifact_digest)
        self._record_timeout(timeout_seconds)
        return [page for page in self.pages if page.get("run_id") == run_id]

    def create_page(
        self,
        target_id: str,
        run_id: str,
        artifact_digest: str,
        attachment_ids: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> JSONMap:
        _ = (target_id, attachment_ids)
        self._record_timeout(timeout_seconds)
        self.create_page_calls += 1
        page: JSONMap = {
            "id": "page-created",
            "target_id": target_id,
            "run_id": run_id,
            "artifact_digest": artifact_digest,
        }
        self.pages.append(page)
        if self.duplicate_after_create:
            self.pages.append(
                {
                    "id": "page-concurrent",
                    "target_id": target_id,
                    "run_id": run_id,
                    "artifact_digest": artifact_digest,
                }
            )
        self.remote_roots = list(self.root_blocks[:100])
        if self.uncertain_page_create:
            raise NotionCreateUncertain(resource="page")
        return page

    def fetch_page(self, page_id: str, *, timeout_seconds: float) -> JSONMap:
        _ = page_id
        self._record_timeout(timeout_seconds)
        return self.actual_page

    def verify_page_parent(
        self, page_id: str, target_id: str, *, timeout_seconds: float
    ) -> None:
        _ = page_id
        self._record_timeout(timeout_seconds)
        self.append_events.append("parent")
        parent = self.page_parent
        if parent is None:
            raise ContractError("Notion page parent data source is missing")
        if parent.get("type") != "data_source_id":
            raise ContractError("Notion page parent type is not data_source_id")
        if parent.get("data_source_id") != target_id:
            raise ContractError(
                "Notion page parent data source does not match expected target"
            )

    def page_root_count(self) -> int:
        return len(self.root_blocks)

    def verified_root_count(self, page_id: str, *, timeout_seconds: float) -> int:
        _ = page_id
        self._record_timeout(timeout_seconds)
        if self.remote_roots != self.root_blocks[: len(self.remote_roots)]:
            raise ContractError("Notion page root prefix mismatch")
        return len(self.remote_roots)

    def append_page(
        self, page_id: str, start_index: int, *, timeout_seconds: float
    ) -> JSONMap:
        _ = page_id
        self._record_timeout(timeout_seconds)
        self.append_attempts += 1
        self.append_events.append("patch")
        self.remote_roots.extend(self.root_blocks[start_index : start_index + 100])
        return {"id": page_id}


def _page(title: str) -> JSONMap:
    return {
        "title": title,
        "properties": {},
        "blocks": [
            {"type": "image", "image": {"artifact_role": "thumbnail"}},
            {"type": "paragraph", "plain_text": "body"},
        ],
    }
