from __future__ import annotations

import json

import httpx2
import pytest

from tests._notion_api_test_support import client as _client
from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.notion_api import NotionApiTransport


def test_page_batches_respect_500kb_request_limit_for_korean_text() -> None:
    roots: tuple[JSONMap, ...] = tuple(
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "가" * 2_000}}]
            },
        }
        for _index in range(100)
    )
    counts: list[int] = []
    sizes: list[int] = []

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
        sizes.append(len(request.content))
        return httpx2.Response(200, json={"id": "page"})

    with _client(httpx2.MockTransport(handler)) as client:
        api = NotionApiTransport(client, root_blocks=roots, target_id="ds")
        _ = api.create_page("ds", "run", "digest", (), timeout_seconds=3)
        _ = api.append_page("page", counts[0], timeout_seconds=3)

    assert sum(counts) == 100
    assert counts[0] < 100
    assert all(size <= 500 * 1024 for size in sizes)


def test_page_rejects_single_root_over_1000_block_elements_before_network() -> None:
    rows: list[JSONValue] = [
        {
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": []},
        }
        for _index in range(1_000)
    ]
    root: JSONMap = {
        "object": "block",
        "type": "table",
        "table": {"children": rows},
    }
    called = False

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(500)

    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="request size or element limits"),
    ):
        _ = NotionApiTransport(client, root_blocks=(root,)).create_page(
            "ds", "run", "digest", (), timeout_seconds=3
        )
    assert called is False
