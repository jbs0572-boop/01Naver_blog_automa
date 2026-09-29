from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import (
    ExternalSystem,
    ExternalWriteRequest,
    NotionWriteOperation,
    authorize_notion_operation,
    prepare_external_write,
)
from tools.manifest import verify_manifest
from tools.notion_checkpoint import load_checkpoint
from tools.notion_transport import (
    AttachmentSpec,
    NotionCreateUncertain,
    NotionTransport,
    image_entries,
)
from tools.notion_write_operations import NotionQ2Failure, NotionWriteOperations


def _attachment_name(run_id: str, role: str, sha256: str, path: str) -> str:
    name = f"{run_id}--{role}--{sha256}--{Path(path).name}"
    if len(name.encode("utf-8")) > 900:
        raise ContractError("Notion deterministic upload filename exceeds 900 bytes")
    return name


@dataclass(frozen=True, slots=True)
class ResumableNotionAdapter:
    transport: NotionTransport
    expected_page: JSONMap
    clock: Callable[[], datetime]
    authorizer: Callable[[ExternalWriteRequest, NotionWriteOperation], JSONMap] = (
        authorize_notion_operation
    )
    monotonic_clock: Callable[[], float] = monotonic
    attachment_binder: Callable[[tuple[str, ...]], None] | None = None

    def attachment_specs(
        self, request: ExternalWriteRequest
    ) -> tuple[AttachmentSpec, ...]:
        manifest = verify_manifest(request.root, request.manifest_path)
        return tuple(
            AttachmentSpec(
                request.root / entry.path,
                _attachment_name(request.run_id, entry.role, entry.sha256, entry.path),
                entry.role,
                entry.sha256,
                entry.order,
            )
            for entry in image_entries(manifest.files)
        )

    def write_and_verify(self, request: ExternalWriteRequest) -> JSONMap:
        if request.system is not ExternalSystem.NOTION:
            raise ContractError("resumable Notion adapter only accepts Notion writes")
        if request.dry_run:
            raise ContractError("resumable Notion adapter requires an executing write")
        if request.checkpoint_path is None:
            raise ContractError("resumable Notion adapter requires checkpoint_path")
        checkpoint_path = request.checkpoint_path
        operations = NotionWriteOperations(
            self.transport, self.authorizer, self.monotonic_clock
        )
        deadline = operations.deadline(request)
        plan = prepare_external_write(request)
        checkpoint = load_checkpoint(checkpoint_path, plan)
        page_id = checkpoint.get("page_id")
        if checkpoint.get("q2_status") == "passed":
            if not isinstance(page_id, str):
                raise ContractError("Notion Q2 checkpoint page identity is invalid")
            return operations.verify_q2(
                checkpoint_path,
                plan,
                checkpoint,
                page_id,
                deadline,
                self.expected_page,
                self.clock,
            )
        attachment_ids = tuple(
            operations.reconcile_attachment(
                plan,
                checkpoint_path,
                checkpoint,
                spec,
                request,
                deadline,
            )
            for spec in self.attachment_specs(request)
        )
        if self.attachment_binder is not None:
            self.attachment_binder(attachment_ids)
        page_id = operations.reconcile_page(
            plan,
            checkpoint_path,
            checkpoint,
            attachment_ids,
            request,
            deadline,
        )
        operations.complete_page(
            plan,
            checkpoint_path,
            checkpoint,
            page_id,
            request,
            deadline,
        )
        return operations.verify_q2(
            checkpoint_path,
            plan,
            checkpoint,
            page_id,
            deadline,
            self.expected_page,
            self.clock,
        )


__all__ = [
    "AttachmentSpec",
    "NotionCreateUncertain",
    "NotionQ2Failure",
    "NotionTransport",
    "ResumableNotionAdapter",
]
