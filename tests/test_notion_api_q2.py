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
from tools.contract_types import ContractError, JSONMap
from tools.external_adapter import verify_notion_round_trip
from tools.notion_api import NotionApiTransport
from tools.notion_api_roundtrip import verify_bytes, verify_image
from tools.notion_content import AssetMetadata
from tools.notion_transport import AttachmentSpec


def test_q2_rejects_actual_remote_property_mismatch() -> None:
    properties: JSONMap = {
        "제목": {"title": [{"type": "text", "text": {"content": "title"}}]},
        "모드": {"select": {"name": "정식"}},
        "실행 ID": {"rich_text": [{"type": "text", "text": {"content": "run"}}]},
        "차수": {"number": 1},
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(
                200,
                json={
                    "properties": {
                        "제목": {"title": [{"plain_text": "title"}]},
                        "모드": {"select": {"name": "베타"}},
                        "실행 ID": {"rich_text": [{"plain_text": "wrong"}]},
                        "차수": {"number": 2},
                    }
                },
            )
        return httpx2.Response(200, json={"results": [], "has_more": False})

    with _client(httpx2.MockTransport(handler)) as client:
        actual = NotionApiTransport(client, page_properties=properties).fetch_page(
            "page", timeout_seconds=2
        )
    expected: JSONMap = {"title": "title", "properties": properties, "blocks": []}
    with pytest.raises(ContractError):
        _ = verify_notion_round_trip(
            expected, actual, "page", "2026-08-31T23:59:59+09:00", "digest"
        )


def test_attachment_hash_mismatch_fails_before_network(tmp_path: Path) -> None:
    image = tmp_path / "changed.png"
    _ = image.write_bytes(b"changed")
    called = False

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(500)

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="hash does not match"),
    ):
        _ = NotionApiTransport(client).create_attachment(
            "ds",
            AttachmentSpec(image, "changed.png", "thumbnail", "0" * 64, 1),
            timeout_seconds=2,
        )
    assert called is False


def test_signed_image_download_strips_api_credentials() -> None:
    content = b"verified"

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert "Authorization" not in request.headers
        assert "Notion-Version" not in request.headers
        return httpx2.Response(200, content=content)

    with _client(httpx2.MockTransport(handler)) as client:
        verify_bytes(
            client,
            "https://storage.example/signed",
            AssetMetadata("thumbnail", hashlib.sha256(content).hexdigest(), 1),
            2,
        )


def test_q2_rejects_file_upload_image_without_download_url() -> None:
    block: JSONMap = {
        "type": "image",
        "image": {"type": "file_upload", "file_upload": {"id": "upload"}},
    }

    with (
        _client(httpx2.MockTransport(lambda _request: httpx2.Response(500))) as client,
        pytest.raises(ContractError, match="not finalized for Q2"),
    ):
        verify_image(
            client,
            block,
            AssetMetadata("thumbnail", hashlib.sha256(b"verified").hexdigest(), 1),
            2,
        )


def test_upload_revalidates_schema_immediately_before_send(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")
    paths: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/data_sources/ds":
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        if request.url.path == "/v1/file_uploads":
            return httpx2.Response(200, json={"id": "upload"})
        return httpx2.Response(200, json=_uploaded("upload", "image.png", b"png"))

    with _client(httpx2.MockTransport(handler)) as client:
        _ = NotionApiTransport(client, enforce_schema=True).create_attachment(
            "ds",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"png").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )
    assert paths == [
        "/v1/data_sources/ds",
        "/v1/file_uploads",
        "/v1/data_sources/ds",
        "/v1/file_uploads/upload/send",
    ]


def test_upload_rejects_path_mutation_between_initialize_and_send(
    tmp_path: Path,
) -> None:
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    sent = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal sent
        if request.url.path == "/v1/file_uploads":
            _ = image.write_bytes(b"mutated")
            return httpx2.Response(200, json={"id": "upload"})
        sent = True
        assert b"original" in request.content
        assert b"mutated" not in request.content
        return httpx2.Response(
            200, json=_uploaded("upload", "image.png", b"original")
        )

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="hash does not match"),
    ):
        _ = NotionApiTransport(client).create_attachment(
            "ds",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )
    assert sent is False


def test_concrete_authorizer_failure_before_send_prevents_send(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"original")
    authorizations = 0
    sent = False

    def authorize(_operation: str) -> None:
        nonlocal authorizations
        authorizations += 1
        if authorizations == 2:
            raise ContractError("authorization denied")

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal sent
        if request.url.path == "/v1/file_uploads":
            return httpx2.Response(200, json={"id": "upload"})
        sent = True
        return httpx2.Response(200, json={"id": "upload"})

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="authorization denied"),
    ):
        _ = NotionApiTransport(client, write_authorizer=authorize).create_attachment(
            "ds",
            AttachmentSpec(
                image,
                "image.png",
                "thumbnail",
                hashlib.sha256(b"original").hexdigest(),
                1,
            ),
            timeout_seconds=2,
        )
    assert authorizations == 2
    assert sent is False
