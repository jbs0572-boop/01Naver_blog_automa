from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx2
import pytest

from tests._notion_api_test_support import client as _client
from tests._notion_api_test_support import uploaded as _uploaded
from tools.contract_types import ContractError
from tools.notion_api import NOTION_VERSION, NotionApiTransport
from tools.notion_transport import AttachmentSpec, NotionCreateUncertain


def test_find_attachments_paginates_and_matches_exact_filename() -> None:
    # Given
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.headers["Notion-Version"] == NOTION_VERSION
        cursor = request.url.params.get("start_cursor")
        if cursor is None:
            return httpx2.Response(
                200,
                json={
                    "results": [
                        {
                            "type": "file_upload",
                            "file_upload": {
                                "id": "x",
                                "filename": "other",
                                "status": "uploaded",
                            },
                        },
                        {
                            "type": "file_upload",
                            "file_upload": {
                                "id": "expired",
                                "filename": "exact.png",
                                "status": "expired",
                            },
                        },
                        {
                            "type": "file_upload",
                            "file_upload": {
                                "id": "failed",
                                "filename": "exact.png",
                                "status": "failed",
                            },
                        },
                    ],
                    "has_more": True,
                    "next_cursor": "next",
                },
            )
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "type": "file_upload",
                        "file_upload": {
                            "id": "wanted",
                            "filename": "exact.png",
                            "status": "uploaded",
                            "content_type": "image/png",
                            "content_length": 3,
                        },
                    }
                ],
                "has_more": False,
            },
        )

    transport = httpx2.MockTransport(handler)

    # When
    with _client(transport) as client:
        found = NotionApiTransport(client).find_attachments(
            "ds", "exact.png", timeout_seconds=3
        )

    # Then
    assert found == (_uploaded("wanted", "exact.png", b"png"),)
    assert len(requests) == 2


def test_find_attachments_accepts_direct_file_upload_objects() -> None:
    uploaded = _uploaded("current", "exact.png", b"png")
    response = {
        "results": [{"object": "file_upload", **uploaded}],
        "has_more": False,
    }

    with _client(
        httpx2.MockTransport(lambda _request: httpx2.Response(200, json=response))
    ) as client:
        found = NotionApiTransport(client).find_attachments(
            "ds", "exact.png", timeout_seconds=2
        )

    assert found == (uploaded,)


def test_find_attachments_rejects_legacy_direct_list_shape() -> None:
    # Given
    response = {
        "results": [{"id": "legacy", "filename": "exact.png", "status": "uploaded"}],
        "has_more": False,
    }

    # When / Then
    with (
        _client(
            httpx2.MockTransport(lambda _request: httpx2.Response(200, json=response))
        ) as client,
        pytest.raises(ContractError, match="envelope"),
    ):
        _ = NotionApiTransport(client).find_attachments(
            "ds", "exact.png", timeout_seconds=2
        )


def test_create_attachment_uses_single_part_then_multipart_send(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/v1/file_uploads":
            assert json.loads(request.content) == {
                "mode": "single_part",
                "filename": "safe.png",
                "content_type": "image/png",
            }
            return httpx2.Response(200, json={"id": "upload-1"})
        assert request.url.path == "/v1/file_uploads/upload-1/send"
        assert b'filename="safe.png"' in request.content
        return httpx2.Response(200, json=_uploaded("upload-1", "safe.png", b"png"))

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        created = NotionApiTransport(client).create_attachment(
            "ds",
            AttachmentSpec(
                image,
                "safe.png",
                "thumbnail",
                hashlib.sha256(b"png").hexdigest(),
                1,
            ),
            timeout_seconds=4,
        )

    # Then
    assert created == _uploaded("upload-1", "safe.png", b"png")
    assert [item[:2] for item in seen] == [
        ("POST", "/v1/file_uploads"),
        ("POST", "/v1/file_uploads/upload-1/send"),
    ]


def test_create_attachment_rejects_over_20mb_before_network(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "large.png"
    with image.open("wb") as stream:
        _ = stream.truncate(20 * 1024 * 1024 + 1)
    called = False

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(500)

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="20 MB"),
    ):
        _ = NotionApiTransport(client).create_attachment(
            "ds",
            AttachmentSpec(image, "large.png", "thumbnail", "abc", 1),
            timeout_seconds=4,
        )
    assert called is False


def test_create_timeout_is_uncertain_and_secret_is_redacted(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("secret-token", request=request)

    # When / Then
    with _client(httpx2.MockTransport(handler)) as client:
        api = NotionApiTransport(client)
        with pytest.raises(NotionCreateUncertain) as raised:
            _ = api.create_attachment(
                "ds",
                AttachmentSpec(
                    image,
                    "safe.png",
                    "thumbnail",
                    hashlib.sha256(b"png").hexdigest(),
                    1,
                ),
                timeout_seconds=4,
            )
    assert "secret-token" not in str(raised.value)
