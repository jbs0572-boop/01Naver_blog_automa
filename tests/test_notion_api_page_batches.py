from __future__ import annotations

import json

import httpx2
import pytest

from tests._notion_api_test_support import (
    client as _client,
)
from tests._notion_api_test_support import (
    schema_properties as _schema_properties,
)
from tools.contract_types import ContractError, JSONMap
from tools.notion_api import NotionApiTransport


def test_page_create_and_append_use_root_batches_of_at_most_100() -> None:
    # Given
    roots: tuple[JSONMap, ...] = tuple(
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": []},
            "index": index,
        }
        for index in range(205)
    )
    counts: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(
                200,
                json={
                    "parent": {"type": "data_source_id", "data_source_id": "ds"}
                },
            )
        body = json.loads(request.content)
        counts.append(len(body["children"]))
        return httpx2.Response(200, json={"id": "page"})

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        api = NotionApiTransport(client, root_blocks=roots, target_id="ds")
        _ = api.create_page("ds", "run", "digest", (), timeout_seconds=3)
        _ = api.append_page("page", 100, timeout_seconds=3)
        _ = api.append_page("page", 200, timeout_seconds=3)

    # Then
    assert counts == [100, 100, 5]


def test_append_parent_verification_checks_schema_before_reading_page() -> None:
    # Given
    calls: list[tuple[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/data_sources/ds":
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(
                200,
                json={
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": "other-source",
                    }
                },
            )
        pytest.fail(f"unexpected Notion request: {request.method} {request.url.path}")

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="does not match expected target"),
    ):
        NotionApiTransport(
            client, target_id="ds", enforce_schema=True
        ).verify_page_parent("page", "ds", timeout_seconds=3)
    assert calls == [
        ("GET", "/v1/data_sources/ds"),
        ("GET", "/v1/pages/page"),
    ]
