from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import (
    ExternalWritePlan,
    ExternalWriteRequest,
    NotionWriteOperation,
    verify_notion_round_trip,
)
from tools.notion_checkpoint import (
    reconcile_attachment,
    single_page_match,
)
from tools.notion_resume_authorization import authorize_create, operation_deadline
from tools.notion_transport import (
    AttachmentSpec,
    NotionCreateUncertain,
    NotionTransport,
)
from tools.runner_state import atomic_write_json


class NotionQ2Failure(ContractError):
    pass


@dataclass(frozen=True, slots=True)
class NotionWriteOperations:
    transport: NotionTransport
    authorizer: Callable[[ExternalWriteRequest, NotionWriteOperation], JSONMap]
    monotonic_clock: Callable[[], float]

    def deadline(self, request: ExternalWriteRequest) -> float:
        return operation_deadline(self.monotonic_clock, request.notion_timeout_seconds)

    def remaining_timeout(self, deadline: float) -> float:
        remaining = deadline - self.monotonic_clock()
        if remaining <= 0:
            raise ContractError("Notion overall deadline is exhausted")
        return remaining

    def reconcile_attachment(
        self,
        plan: ExternalWritePlan,
        checkpoint_path: Path,
        checkpoint: JSONMap,
        spec: AttachmentSpec,
        request: ExternalWriteRequest,
        deadline: float,
    ) -> str:
        attachment_id = reconcile_attachment(
            self.transport,
            self.authorizer,
            lambda: self.remaining_timeout(deadline),
            plan,
            checkpoint,
            spec,
            request,
            lambda: atomic_write_json(checkpoint_path, checkpoint),
        )
        atomic_write_json(checkpoint_path, checkpoint)
        return attachment_id

    def reconcile_page(
        self,
        plan: ExternalWritePlan,
        checkpoint_path: Path,
        checkpoint: JSONMap,
        attachment_ids: tuple[str, ...],
        request: ExternalWriteRequest,
        deadline: float,
    ) -> str:
        page_id = checkpoint.get("page_id")
        if isinstance(page_id, str) and page_id:
            recorded = single_page_match(
                self.transport.find_pages(
                    plan.target_id,
                    plan.run_id,
                    plan.artifact_digest,
                    timeout_seconds=self.remaining_timeout(deadline),
                ),
                plan,
            )
            if recorded != page_id:
                raise ContractError("Notion recorded page identity is unavailable")
            return page_id
        if isinstance(page_id, str):
            raise ContractError("Notion recorded page identity is invalid")
        if checkpoint.get("page_create_pending") is True:
            existing = single_page_match(
                self.transport.find_pages(
                    plan.target_id,
                    plan.run_id,
                    plan.artifact_digest,
                    timeout_seconds=self.remaining_timeout(deadline),
                ),
                plan,
            )
            if existing is None:
                raise ContractError(
                    "uncertain Notion page create could not be reconciled"
                )
            checkpoint["page_id"] = existing
            checkpoint["page_create_pending"] = False
            atomic_write_json(checkpoint_path, checkpoint)
            return existing
        existing = single_page_match(
            self.transport.find_pages(
                plan.target_id,
                plan.run_id,
                plan.artifact_digest,
                timeout_seconds=self.remaining_timeout(deadline),
            ),
            plan,
        )
        if existing is not None:
            raise ContractError(
                "pre-existing Notion run page requires manual recovery"
            )
        authorize_create(self.authorizer, request, plan, "create_page")
        timeout_seconds = self.remaining_timeout(deadline)
        checkpoint["page_create_pending"] = True
        atomic_write_json(checkpoint_path, checkpoint)
        try:
            created = self.transport.create_page(
                plan.target_id,
                plan.run_id,
                plan.artifact_digest,
                attachment_ids,
                timeout_seconds=timeout_seconds,
            )
            page_id = single_page_match((created,), plan)
            if page_id is None:
                raise ContractError("Notion created page identity is missing")
        except NotionCreateUncertain:
            page_id = single_page_match(
                self.transport.find_pages(
                    plan.target_id,
                    plan.run_id,
                    plan.artifact_digest,
                    timeout_seconds=self.remaining_timeout(deadline),
                ),
                plan,
            )
            if page_id is None:
                raise ContractError(
                    "uncertain Notion page create could not be reconciled"
                ) from None
        checkpoint["page_id"] = page_id
        checkpoint["page_create_pending"] = False
        checkpoint["appended_root_count"] = self.transport.verified_root_count(
            page_id, timeout_seconds=self.remaining_timeout(deadline)
        )
        atomic_write_json(checkpoint_path, checkpoint)
        return page_id

    def complete_page(
        self,
        plan: ExternalWritePlan,
        checkpoint_path: Path,
        checkpoint: JSONMap,
        page_id: str,
        request: ExternalWriteRequest,
        deadline: float,
    ) -> None:
        total = self.transport.page_root_count()
        recorded = checkpoint.get("appended_root_count", 0)
        if not isinstance(recorded, int) or recorded < 0 or recorded > total:
            raise ContractError("Notion appended root checkpoint is invalid")
        actual = self.transport.verified_root_count(
            page_id, timeout_seconds=self.remaining_timeout(deadline)
        )
        if actual < recorded or actual > total:
            raise ContractError("Notion page root prefix does not match this write")
        checkpoint["appended_root_count"] = actual
        while actual < total:
            self.transport.verify_page_parent(
                page_id,
                plan.target_id,
                timeout_seconds=self.remaining_timeout(deadline),
            )
            authorize_create(self.authorizer, request, plan, "create_page")
            checkpoint["append_pending_at"] = actual
            atomic_write_json(checkpoint_path, checkpoint)
            try:
                _ = self.transport.append_page(
                    page_id, actual, timeout_seconds=self.remaining_timeout(deadline)
                )
            except NotionCreateUncertain:
                pass
            reconciled = self.transport.verified_root_count(
                page_id, timeout_seconds=self.remaining_timeout(deadline)
            )
            if reconciled <= actual:
                raise ContractError(
                    "uncertain Notion page append could not be reconciled"
                )
            actual = reconciled
            checkpoint["appended_root_count"] = actual
            checkpoint["append_pending_at"] = None
            atomic_write_json(checkpoint_path, checkpoint)

    def verify_q2(
        self,
        checkpoint_path: Path,
        plan: ExternalWritePlan,
        checkpoint: JSONMap,
        page_id: str,
        deadline: float,
        expected_page: JSONMap,
        clock: Callable[[], datetime],
    ) -> JSONMap:
        try:
            recorded = single_page_match(
                self.transport.find_pages(
                    plan.target_id,
                    plan.run_id,
                    plan.artifact_digest,
                    timeout_seconds=self.remaining_timeout(deadline),
                ),
                plan,
            )
            if recorded != page_id:
                raise ContractError("Notion recorded page identity is unavailable")
            verified_at = clock()
            if verified_at.tzinfo is None or verified_at.utcoffset() is None:
                raise ContractError(
                    "Notion verification timestamp must include a timezone"
                )
            result = verify_notion_round_trip(
                expected_page,
                self.transport.fetch_page(
                    page_id, timeout_seconds=self.remaining_timeout(deadline)
                ),
                page_id,
                verified_at.isoformat(),
                plan.artifact_digest,
            )
        except ContractError as error:
            checkpoint["q2_status"] = "failed"
            checkpoint["q2_error"] = str(error)
            atomic_write_json(checkpoint_path, checkpoint)
            raise NotionQ2Failure(str(error)) from error
        checkpoint["q2_status"] = "passed"
        checkpoint["q2_result"] = result
        _ = checkpoint.pop("q2_error", None)
        atomic_write_json(checkpoint_path, checkpoint)
        return result


__all__ = ["NotionQ2Failure", "NotionWriteOperations"]
