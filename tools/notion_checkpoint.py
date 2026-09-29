from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.external_adapter import (
    ExternalWritePlan,
    ExternalWriteRequest,
    NotionWriteOperation,
)
from tools.notion_api_uploads import expected_upload_metadata, upload_metadata
from tools.notion_resume_authorization import authorize_create
from tools.notion_transport import (
    AttachmentSpec,
    NotionCreateUncertain,
    NotionTransport,
)


def identifier(value: JSONMap, resource: str) -> str:
    result = value.get("id")
    if not isinstance(result, str) or not result:
        raise ContractError(f"Notion {resource} identity is missing")
    return result


def single_match(values: Sequence[JSONMap], resource: str) -> str | None:
    if len(values) > 1:
        raise ContractError(f"Notion {resource} identity is ambiguous")
    return identifier(values[0], resource) if values else None


def single_active_attachment(values: Sequence[JSONMap]) -> JSONMap | None:
    if len(values) > 1:
        raise ContractError("Notion attachment identity is ambiguous")
    if not values:
        return None
    value = values[0]
    status = value.get("status")
    if not isinstance(status, str) or status not in {"pending", "uploaded"}:
        raise ContractError("Notion attachment status is invalid")
    result: JSONMap = {"id": identifier(value, "attachment"), "status": status}
    if status == "uploaded":
        result.update(
            {
                "filename": value.get("filename"),
                "content_type": value.get("content_type"),
                "content_length": value.get("content_length"),
            }
        )
    return result


def attachment_record(value: JSONValue, spec: AttachmentSpec) -> tuple[str | None, str]:
    if not isinstance(value, dict):
        raise ContractError("Notion attachment provenance record is invalid")
    if value.get("sha256") != spec.sha256:
        raise ContractError("Notion attachment provenance hash does not match manifest")
    status = value.get("status")
    if not isinstance(status, str) or status not in {
        "initializing",
        "pending",
        "uploaded",
    }:
        raise ContractError("Notion attachment provenance status is invalid")
    upload_id = value.get("upload_id")
    if status == "initializing":
        if upload_id is not None:
            raise ContractError("Notion initializing attachment identity is invalid")
        return None, status
    if not isinstance(upload_id, str) or not upload_id:
        raise ContractError("Notion attachment provenance identity is invalid")
    if status == "uploaded":
        verify_uploaded_metadata(value, spec)
    return upload_id, status


def provenance_record(spec: AttachmentSpec, upload_id: str | None, status: str) -> JSONMap:
    record: JSONMap = {
        "upload_id": upload_id,
        "sha256": spec.sha256,
        "status": status,
    }
    if status == "uploaded":
        record.update(expected_upload_metadata(spec))
    return record


def verify_uploaded_metadata(active: JSONMap, spec: AttachmentSpec) -> None:
    if upload_metadata(active) != expected_upload_metadata(spec):
        raise ContractError("Notion uploaded attachment metadata does not match manifest")


def reconcile_attachment(
    transport: NotionTransport,
    authorizer: Callable[[ExternalWriteRequest, NotionWriteOperation], JSONMap],
    remaining_timeout: Callable[[], float],
    plan: ExternalWritePlan,
    checkpoint: JSONMap,
    spec: AttachmentSpec,
    request: ExternalWriteRequest,
    checkpoint_writer: Callable[[], None],
) -> str:
    attachments = checkpoint["attachments"]
    if not isinstance(attachments, dict):
        raise ContractError("Notion checkpoint attachments are invalid")
    recorded = attachments.get(spec.name)
    if recorded is None and spec.name not in attachments:
        remote = single_active_attachment(
            transport.find_attachments(
                plan.target_id, spec.name, timeout_seconds=remaining_timeout()
            )
        )
        if remote is not None:
            raise ContractError(
                "Notion filename-only attachment provenance cannot be proven; manual recovery is required"
            )
        authorize_create(authorizer, request, plan, "create_attachment")
        attachments[spec.name] = provenance_record(spec, None, "initializing")
        checkpoint_writer()
        try:
            active = single_active_attachment(
                (
                    transport.initialize_attachment(
                        plan.target_id, spec, timeout_seconds=remaining_timeout()
                    ),
                )
            )
        except NotionCreateUncertain:
            raise ContractError(
                "uncertain Notion attachment initialize requires manual recovery"
            ) from None
        if active is None or active.get("status") != "pending":
            raise ContractError("Notion initialized attachment is not pending")
        upload_id = identifier(active, "attachment")
        attachments[spec.name] = provenance_record(spec, upload_id, "pending")
        checkpoint_writer()
    else:
        upload_id, recorded_status = attachment_record(recorded, spec)
        if recorded_status == "initializing":
            raise ContractError(
                "uncertain Notion attachment initialize requires manual recovery"
            )
        if upload_id is None:
            raise ContractError("Notion attachment provenance identity is missing")
        active = single_active_attachment(
            transport.find_attachments(
                plan.target_id, spec.name, timeout_seconds=remaining_timeout()
            )
        )
        if active is None or identifier(active, "attachment") != upload_id:
            raise ContractError("Notion recorded attachment identity is unavailable")
        if recorded_status == "uploaded" and active.get("status") != "uploaded":
            raise ContractError("Notion recorded attachment upload regressed")
        if active.get("status") == "uploaded":
            verify_uploaded_metadata(active, spec)
            attachments[spec.name] = provenance_record(spec, upload_id, "uploaded")
            checkpoint_writer()
            return upload_id
    if active.get("status") == "pending":
        authorize_create(authorizer, request, plan, "create_attachment")
        active = single_active_attachment(
            (
                transport.complete_attachment(
                    plan.target_id,
                    identifier(active, "attachment"),
                    spec,
                    timeout_seconds=remaining_timeout(),
                ),
            )
        )
    if active is None or active.get("status") != "uploaded":
        raise ContractError("Notion attachment did not upload")
    attachment_id = identifier(active, "attachment")
    if attachment_id != upload_id:
        raise ContractError("Notion attachment upload identity does not match provenance")
    verify_uploaded_metadata(active, spec)
    attachments[spec.name] = provenance_record(spec, attachment_id, "uploaded")
    checkpoint_writer()
    return attachment_id


def single_page_match(
    values: Sequence[JSONMap], plan: ExternalWritePlan
) -> str | None:
    page_id = single_match(values, "page")
    if page_id is None:
        return None
    page = values[0]
    target_id = page.get("target_id")
    if (
        page.get("run_id") != plan.run_id
        or page.get("artifact_digest") != plan.artifact_digest
        or target_id != plan.target_id
    ):
        raise ContractError("Notion page identity does not match this write")
    return page_id


def load_checkpoint(path: Path, plan: ExternalWritePlan) -> JSONMap:
    if not path.is_file():
        return {
            "version": 1,
            "run_id": plan.run_id,
            "target_id": plan.target_id,
            "artifact_digest": plan.artifact_digest,
            "attachments": {},
            "page_id": None,
            "page_create_pending": False,
            "appended_root_count": 0,
            "append_pending_at": None,
            "q2_status": "pending",
            "q2_result": None,
        }
    try:
        value: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
        raise ContractError(f"could not read Notion checkpoint: {path}") from error
    if not isinstance(value, dict):
        raise ContractError("Notion checkpoint must be an object")
    if (
        value.get("version") != 1
        or value.get("run_id") != plan.run_id
        or value.get("target_id") != plan.target_id
        or value.get("artifact_digest") != plan.artifact_digest
    ):
        raise ContractError("Notion checkpoint identity does not match this write")
    attachments = value.get("attachments")
    if not isinstance(attachments, dict):
        raise ContractError("Notion checkpoint attachments are invalid")
    for name, record in attachments.items():
        if not name or not isinstance(record, dict):
            raise ContractError("Notion attachment provenance record is invalid")
        upload_id = record.get("upload_id")
        sha256 = record.get("sha256")
        status = record.get("status")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or status not in {"initializing", "pending", "uploaded"}
            or (
                status == "initializing"
                and upload_id is not None
            )
            or (
                status != "initializing"
                and (not isinstance(upload_id, str) or not upload_id)
            )
        ):
            raise ContractError("Notion attachment provenance record is invalid")
    count = value.get("appended_root_count", 0)
    if not isinstance(count, int) or isinstance(count, bool):
        raise ContractError("Notion checkpoint appended root count is invalid")
    return value


__all__ = [
    "attachment_record",
    "identifier",
    "load_checkpoint",
    "reconcile_attachment",
    "single_active_attachment",
    "single_match",
    "single_page_match",
]
