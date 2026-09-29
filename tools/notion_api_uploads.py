from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Callable

import httpx2

from tools.contract_types import ContractError, JSONMap
from tools.notion_api_http import NotionHttp, array, as_map, text
from tools.notion_transport import AttachmentSpec


def find_uploads(
    http: NotionHttp, target_id: str, name: str, timeout: float
) -> tuple[JSONMap, ...]:
    if not target_id:
        raise ContractError("Notion target identity is missing")
    results: list[JSONMap] = []
    cursor: str | None = None
    while True:
        params = {"page_size": "100"}
        if cursor:
            params["start_cursor"] = cursor
        page = http.read("GET", "/v1/file_uploads", timeout, params=params)
        for raw in array(page, "results"):
            envelope = as_map(raw, "file upload")
            if envelope.get("object") == "file_upload":
                item = envelope
            elif envelope.get("type") == "file_upload":
                item = as_map(envelope.get("file_upload"), "file upload payload")
            else:
                raise ContractError("Notion file upload envelope type is invalid")
            if item.get("filename") != name:
                continue
            status = text(item, "status")
            if status in {"expired", "failed"}:
                continue
            if status not in {"pending", "uploaded"}:
                raise ContractError("Notion file upload status is invalid")
            result: JSONMap = {"id": text(item, "id"), "status": status}
            if status == "uploaded":
                result.update(upload_metadata(item))
            results.append(result)
        if page.get("has_more") is not True:
            return tuple(results)
        cursor = text(page, "next_cursor")


def create_upload(
    client: httpx2.Client,
    http: NotionHttp,
    target_id: str,
    spec: AttachmentSpec,
    timeout: float,
    validate_schema: Callable[[str], None] | None,
    authorize: Callable[[], None] | None,
    identities: dict[str, str],
) -> JSONMap:
    pending = initialize_upload(
        http,
        target_id,
        spec,
        timeout,
        validate_schema,
        authorize,
    )
    upload_id = text(pending, "id")
    verified_bytes = verify_local_attachment(spec)
    payload = send_upload(
        client,
        http,
        target_id,
        upload_id,
        spec,
        verified_bytes,
        timeout,
        validate_schema,
        authorize,
    )
    attachment_id = text(payload, "id")
    identities[spec.path.name] = attachment_id
    return payload


def initialize_upload(
    http: NotionHttp,
    target_id: str,
    spec: AttachmentSpec,
    timeout: float,
    validate_schema: Callable[[str], None] | None,
    authorize: Callable[[], None] | None,
) -> JSONMap:
    _ = verify_local_attachment(spec)
    content_type = mimetypes.guess_type(spec.name)[0] or "application/octet-stream"

    def validate_and_authorize() -> None:
        if validate_schema is not None:
            validate_schema(target_id)
        if authorize is not None:
            authorize()

    created = http.create(
        "POST",
        "/v1/file_uploads",
        timeout,
        json_body={
            "mode": "single_part",
            "filename": spec.name,
            "content_type": content_type,
        },
        resource="attachment",
        authorize=validate_and_authorize,
    )
    upload_id = text(created, "id")
    return {"id": upload_id, "status": "pending"}


def complete_upload(
    client: httpx2.Client,
    http: NotionHttp,
    target_id: str,
    upload_id: str,
    spec: AttachmentSpec,
    timeout: float,
    validate_schema: Callable[[str], None] | None,
    authorize: Callable[[], None] | None,
    identities: dict[str, str],
) -> JSONMap:
    verified_bytes = verify_local_attachment(spec)
    payload = send_upload(
        client,
        http,
        target_id,
        upload_id,
        spec,
        verified_bytes,
        timeout,
        validate_schema,
        authorize,
    )
    attachment_id = text(payload, "id")
    identities[spec.path.name] = attachment_id
    return payload


def send_upload(
    client: httpx2.Client,
    http: NotionHttp,
    target_id: str,
    upload_id: str,
    spec: AttachmentSpec,
    verified_bytes: bytes,
    timeout: float,
    validate_schema: Callable[[str], None] | None,
    authorize: Callable[[], None] | None,
) -> JSONMap:
    content_type = mimetypes.guess_type(spec.name)[0] or "application/octet-stream"

    def send_once(remaining: float) -> httpx2.Response:
        if validate_schema is not None:
            validate_schema(target_id)
        if authorize is not None:
            authorize()
        return client.post(
            f"/v1/file_uploads/{upload_id}/send",
            files={"file": (spec.name, verified_bytes, content_type)},
            timeout=remaining,
        )

    payload = http.send(timeout, "attachment", send_once)
    if payload.get("status") != "uploaded":
        raise ContractError("Notion file upload did not complete")
    if text(payload, "id") != upload_id:
        raise ContractError("Notion file upload identity does not match")
    metadata = upload_metadata(payload)
    content_length = metadata["content_length"]
    expected_type = mimetypes.guess_type(spec.name)[0] or "application/octet-stream"
    if (
        metadata["filename"] != spec.name
        or metadata["content_type"] != expected_type
        or content_length != len(verified_bytes)
    ):
        raise ContractError("Notion uploaded attachment metadata does not match manifest")
    return {"id": upload_id, "status": "uploaded", **metadata}


def upload_metadata(payload: JSONMap) -> JSONMap:
    filename = payload.get("filename")
    content_type = payload.get("content_type")
    content_length = payload.get("content_length")
    if (
        not isinstance(filename, str)
        or not filename
        or not isinstance(content_type, str)
        or not content_type
        or isinstance(content_length, bool)
        or not isinstance(content_length, int)
        or content_length < 0
    ):
        raise ContractError("Notion uploaded attachment metadata is invalid")
    return {
        "filename": filename,
        "content_type": content_type,
        "content_length": content_length,
    }


def expected_upload_metadata(spec: AttachmentSpec) -> JSONMap:
    content = verify_local_attachment(spec)
    return {
        "filename": spec.name,
        "content_type": mimetypes.guess_type(spec.name)[0]
        or "application/octet-stream",
        "content_length": len(content),
    }


def verify_local_attachment(spec: AttachmentSpec) -> bytes:
    try:
        size = spec.path.stat().st_size
    except OSError as error:
        raise ContractError("Notion attachment could not be verified") from error
    if size > 20 * 1024 * 1024:
        raise ContractError("Notion single-part upload exceeds 20 MB")
    try:
        content = spec.path.read_bytes()
    except OSError as error:
        raise ContractError("Notion attachment could not be verified") from error
    if len(content) > 20 * 1024 * 1024:
        raise ContractError("Notion single-part upload exceeds 20 MB")
    if hashlib.sha256(content).hexdigest() != spec.sha256:
        raise ContractError("Notion attachment hash does not match manifest")
    return content
