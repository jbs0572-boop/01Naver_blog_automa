from __future__ import annotations

import httpx2
import pytest

from tests._notion_api_test_support import (
    client as _client,
)
from tests._notion_api_test_support import (
    schema_properties as _schema_properties,
)
from tools.contract_types import ContractError
from tools.notion_api import NotionApiTransport


def test_append_retry_fails_closed_when_parent_changes_after_rate_limit() -> None:
    # Given
    schema_reads = 0
    parent_reads = 0
    authorizations = 0
    writes = 0

    def authorize(operation: str) -> None:
        nonlocal authorizations
        assert operation == "create_page"
        authorizations += 1

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, parent_reads, writes
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        if request.url.path == "/v1/pages/page":
            parent_reads += 1
            parent_id = "ds" if parent_reads == 1 else "other-source"
            return httpx2.Response(
                200,
                json={
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": parent_id,
                    }
                },
            )
        assert request.url.path == "/v1/blocks/page/children"
        writes += 1
        return httpx2.Response(429, headers={"Retry-After": "0"})

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="does not match expected target"),
    ):
        _ = NotionApiTransport(
            client,
            root_blocks=(
                {
                    "type": "paragraph",
                    "paragraph": {"rich_text": []},
                },
            ),
            target_id="ds",
            enforce_schema=True,
            write_authorizer=authorize,
        ).append_page("page", 0, timeout_seconds=3)
    assert (schema_reads, parent_reads, authorizations, writes) == (2, 2, 1, 1)


def test_append_retry_rechecks_parent_and_authorizes_each_patch_attempt() -> None:
    # Given
    schema_reads = 0
    parent_reads = 0
    authorizations = 0
    writes = 0

    def authorize(operation: str) -> None:
        nonlocal authorizations
        assert operation == "create_page"
        authorizations += 1

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, parent_reads, writes
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            return httpx2.Response(
                200, json={"id": "ds", "properties": _schema_properties()}
            )
        if request.url.path == "/v1/pages/page":
            parent_reads += 1
            return httpx2.Response(
                200,
                json={"parent": {"type": "data_source_id", "data_source_id": "ds"}},
            )
        assert request.url.path == "/v1/blocks/page/children"
        writes += 1
        if writes == 1:
            return httpx2.Response(429, headers={"Retry-After": "0"})
        return httpx2.Response(200, json={"id": "page"})

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        result = NotionApiTransport(
            client,
            root_blocks=(
                {
                    "type": "paragraph",
                    "paragraph": {"rich_text": []},
                },
            ),
            target_id="ds",
            enforce_schema=True,
            write_authorizer=authorize,
        ).append_page("page", 0, timeout_seconds=3)

    # Then
    assert result == {"id": "page"}
    assert (schema_reads, parent_reads, authorizations, writes) == (2, 2, 2, 2)


def test_page_create_retry_stops_when_schema_changes_after_rate_limit() -> None:
    # Given
    schema_reads = 0
    authorizations = 0
    writes = 0

    def authorize(operation: str) -> None:
        nonlocal authorizations
        assert operation == "create_page"
        authorizations += 1

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal schema_reads, writes
        if request.url.path == "/v1/data_sources/ds":
            schema_reads += 1
            properties = _schema_properties()
            if schema_reads == 2:
                properties["제목"] = {"type": "rich_text"}
            return httpx2.Response(200, json={"id": "ds", "properties": properties})
        assert request.url.path == "/v1/pages"
        writes += 1
        return httpx2.Response(429, headers={"Retry-After": "0"})

    # When / Then
    with (
        _client(httpx2.MockTransport(handler)) as client,
        pytest.raises(ContractError, match="제목"),
    ):
        _ = NotionApiTransport(
            client,
            target_id="ds",
            enforce_schema=True,
            write_authorizer=authorize,
        ).create_page("ds", "run", "digest", (), timeout_seconds=3)
    assert (schema_reads, authorizations, writes) == (2, 1, 1)
