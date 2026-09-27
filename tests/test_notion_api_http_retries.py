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
from tools.notion_api_http import NotionHttp
from tools.notion_transport import AttachmentSpec, NotionCreateUncertain


def test_read_retries_529_and_honors_retry_after() -> None:
    calls = 0
    waits: list[float] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(529, headers={"Retry-After": "0.25"})
        return httpx2.Response(200, json={"ok": True})

    with _client(httpx2.MockTransport(handler)) as client:
        result = NotionHttp(client, sleeper=waits.append, clock=lambda: 0.0).read(
            "GET", "/v1/data_sources/ds", 2.0
        )
    assert result == {"ok": True}
    assert calls == 2
    assert waits == [0.25]


def test_create_retries_429_and_529_within_budget() -> None:
    responses = iter(
        [
            httpx2.Response(429, headers={"Retry-After": "0"}),
            httpx2.Response(200, json={"id": "created"}),
        ]
    )
    with _client(httpx2.MockTransport(lambda _request: next(responses))) as client:
        value = NotionHttp(
            client, sleeper=lambda _delay: None, clock=lambda: 0.0
        ).create("POST", "/v1/pages", 2.0, json_body={}, resource="page")
    assert value == {"id": "created"}

    calls = 0

    def uncertain(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(529, headers={"Retry-After": "0"})

    with (
        _client(httpx2.MockTransport(uncertain)) as client,
        pytest.raises(NotionCreateUncertain),
    ):
        _ = NotionHttp(client, sleeper=lambda _delay: None, clock=lambda: 0.0).create(
            "POST", "/v1/pages", 2.0, json_body={}, resource="page"
        )
    assert calls == 4


def test_upload_send_retries_explicit_429_with_retry_after(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")
    send_calls = 0
    waits: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal send_calls
        if request.url.path == "/v1/file_uploads":
            return httpx2.Response(200, json={"id": "upload"})
        send_calls += 1
        if send_calls == 1:
            return httpx2.Response(429, headers={"Retry-After": "0.2"})
        return httpx2.Response(200, json=_uploaded("upload", "image.png", b"png"))

    with _client(httpx2.MockTransport(handler)) as client:
        http = NotionHttp(client, sleeper=waits.append, clock=lambda: 0.0)
        result = NotionApiTransport(client, http=http).create_attachment(
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
    assert result == _uploaded("upload", "image.png", b"png")
    assert send_calls == 2
    assert waits == [0.2]


def test_upload_initialize_revalidates_schema_before_retry(tmp_path: Path) -> None:
    # Given
    image = tmp_path / "image.png"
    _ = image.write_bytes(b"png")
    schema_reads = 0
    initialize_posts = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, initialize_posts
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            properties = _schema_properties()
            if schema_reads == 2:
                properties["검수 완료일"] = {"type": "rich_text"}
            return httpx2.Response(200, json={"id": "ds", "properties": properties})
        initialize_posts += 1
        return httpx2.Response(429, headers={"Retry-After": "0"})

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="검수 완료일"),
    ):
        _ = NotionApiTransport(client, enforce_schema=True).initialize_attachment(
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
    assert schema_reads == 2
    assert initialize_posts == 1
