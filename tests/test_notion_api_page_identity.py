from __future__ import annotations

import json

import httpx2

from tests._notion_api_test_support import client as _client
from tools.notion_api import NotionApiTransport


def test_find_pages_extracts_digest_from_review_note() -> None:
    # Given
    digest = "sha256:" + "a" * 64

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/data_sources/ds/query"
        assert json.loads(request.content)["filter"]["property"] == "실행 ID"
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "id": "page",
                        "created_time": "2026-08-31T15:00:00.000Z",
                        "parent": {"data_source_id": "ds"},
                        "properties": {
                            "제목": {
                                "title": [
                                    {
                                        "plain_text": "네이버-블로그-글쓰기-2026-08-31-2차수"
                                    }
                                ]
                            },
                            "실행일": {"date": {"start": "2026-08-31"}},
                            "차수": {"number": 2},
                            "실행 ID": {"rich_text": [{"plain_text": "run"}]},
                            "검수 메모": {
                                "rich_text": [
                                    {"plain_text": f"artifact_digest={digest}"}
                                ]
                            },
                        },
                    }
                ],
                "has_more": False,
            },
        )

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        pages = NotionApiTransport(client).find_pages(
            "ds", "run", digest, timeout_seconds=3
        )

    # Then
    assert pages == (
        {
            "id": "page",
            "created_time": "2026-08-31T15:00:00.000Z",
            "target_id": "ds",
            "run_id": "run",
            "artifact_digest": digest,
            "title": "네이버-블로그-글쓰기-2026-08-31-2차수",
            "execution_date": "2026-08-31",
            "sequence": 2,
        },
    )


def test_find_pages_returns_same_run_page_with_another_artifact_digest() -> None:
    # Given
    current = "sha256:" + "a" * 64
    stale = "sha256:" + "b" * 64

    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "results": [
                    {
                        "id": "stale-page",
                        "created_time": "2026-08-31T15:00:00.000Z",
                        "properties": {
                            "제목": {"title": [{"plain_text": "title"}]},
                            "실행일": {"date": {"start": "2026-08-31"}},
                            "차수": {"number": 1},
                            "검수 메모": {
                                "rich_text": [
                                    {"plain_text": f"artifact_digest={stale}; Q1=passed"}
                                ]
                            },
                        },
                    }
                ],
                "has_more": False,
            },
        )

    # When
    with _client(httpx2.MockTransport(handler)) as client:
        pages = NotionApiTransport(client).find_pages(
            "ds", "run", current, timeout_seconds=3
        )

    # Then
    assert len(pages) == 1
    assert pages[0]["artifact_digest"] == stale
