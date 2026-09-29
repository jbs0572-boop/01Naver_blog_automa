from __future__ import annotations

import hashlib
from pathlib import Path

import httpx2
import pytest

from tests._notion_api_test_support import (
    client as _client,
)
from tests._notion_api_test_support import (
    schema_properties as _schema_properties,
)
from tests._notion_api_test_support import (
    uploaded as _uploaded,
)
from tools.contract_types import ContractError
from tools.notion_api import NotionApiTransport
from tools.notion_transport import AttachmentSpec, NotionCreateUncertain


def test_pending_upload_is_completed_without_duplicate_create(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        assert b"original" in request.content
        return httpx2.Response(
            200, json=_uploaded("pending", "image.png", b"original")
        )

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        completed = NotionApiTransport(client).complete_attachment(
            "ds",
            "pending",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )

    # Then
    assert completed == _uploaded("pending", "image.png", b"original")
    assert paths == ["/v1/file_uploads/pending/send"]


def test_pending_upload_requires_uploaded_status_after_send(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")

    # When / Then
    with (
        _client(
            httpx2.MockTransport(
                lambda _request: httpx2.Response(
                    200, json={"id": "pending", "status": "pending"}
                )
            )
        ) as client,
        pytest.raises(ContractError, match="did not complete"),
    ):
        _ = NotionApiTransport(client).complete_attachment(
            "ds",
            "pending",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("filename", "other.png"),
        ("content_type", "application/octet-stream"),
        ("content_length", 999),
        ("content_length", True),
    ],
)
def test_completed_upload_rejects_metadata_mismatch_before_use(
    tmp_path: Path, field: str, value: str | int | bool
) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    payload = _uploaded("pending", "image.png", b"original")
    payload[field] = value

    # When / Then
    with (
        _client(
            httpx2.MockTransport(
                lambda _request: httpx2.Response(200, json=payload)
            )
        ) as client,
        pytest.raises(ContractError, match="metadata"),
    ):
        _ = NotionApiTransport(client).complete_attachment(
            "ds",
            "pending",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )


def test_uncertain_pending_send_is_completed_on_retry_without_create(
    tmp_path: Path,
) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    sends = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal sends
        assert request.url.path == "/v1/file_uploads/pending/send"
        sends += 1
        if sends == 1:
            return httpx2.Response(500)
        return httpx2.Response(
            200, json=_uploaded("pending", "image.png", b"original")
        )

    spec = AttachmentSpec(
        image,
        "image.png",
        "thumbnail",
        hashlib.sha256(b"original").hexdigest(),
        1,
    )

    # When / Then
    with _client(httpx2.MockTransport(handler)) as client:
        transport = NotionApiTransport(client)
        with pytest.raises(NotionCreateUncertain):
            _ = transport.complete_attachment("ds", "pending", spec, timeout_seconds=2)
        completed = transport.complete_attachment(
            "ds", "pending", spec, timeout_seconds=2
        )
    assert completed == _uploaded("pending", "image.png", b"original")
    assert sends == 2


def test_pending_send_revalidates_authorizes_and_uses_verified_bytes_on_retry(
    tmp_path: Path,
) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    authorizations = 0
    schema_reads = 0
    sends = 0

    def authorize(operation: str) -> None:
        nonlocal authorizations
        assert operation == "create_attachment"
        authorizations += 1
        _ = image.write_bytes(b"mutated")

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, sends
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        assert request.url.path == "/v1/file_uploads/pending/send"
        assert b"original" in request.content
        assert b"mutated" not in request.content
        sends += 1
        if sends == 1:
            return httpx2.Response(429, headers={"Retry-After": "0"})
        return httpx2.Response(
            200, json=_uploaded("pending", "image.png", b"original")
        )

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        completed = NotionApiTransport(
            client, enforce_schema=True, write_authorizer=authorize
        ).complete_attachment(
            "ds",
            "pending",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )

    # Then
    assert completed == _uploaded("pending", "image.png", b"original")
    assert authorizations == 2
    assert schema_reads == 2
