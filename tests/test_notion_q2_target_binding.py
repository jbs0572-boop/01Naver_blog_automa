from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from tools.contract_types import JSONMap
from tools.external_adapter import ExternalAction, ExternalSystem, ExternalWritePlan
from tools.notion_api_http import create_notion_client
from tools.notion_api_transport import NotionApiTransport
from tools.notion_content import AssetMetadata
from tools.notion_keychain import NotionApiToken
from tools.notion_write_operations import NotionQ2Failure, NotionWriteOperations


def _plan() -> ExternalWritePlan:
    return ExternalWritePlan(
        system=ExternalSystem.NOTION,
        action=ExternalAction.NOTION_WRITE,
        run_id="RUN-q2-target",
        target_id="data-source-expected",
        artifact_digest="sha256:" + "a" * 64,
        artifact_paths=(),
        dry_run=False,
        would_execute=True,
    )


def _expected_page(content_hash: str) -> JSONMap:
    return {
        "title": "topic",
        "properties": {},
        "blocks": [
            {
                "type": "image",
                "image": {
                    "artifact_role": "thumbnail",
                    "original_sha256": content_hash,
                    "caption": "",
                    "caption_rich_text": [],
                    "order": 1,
                },
            }
        ],
    }


def _query_response() -> JSONMap:
    return {
        "results": [
            {
                "id": "page",
                "properties": {
                    "제목": {"title": [{"plain_text": "topic"}]},
                    "실행일": {"date": {"start": "2026-09-01"}},
                    "차수": {"number": 1},
                    "검수 메모": {
                        "rich_text": [
                            {
                                "plain_text": (
                                    "artifact_digest=" + "sha256:" + "a" * 64
                                )
                            }
                        ]
                    },
                },
            }
        ],
        "has_more": False,
    }


def _verify_q2(
    tmp_path: Path, transport: NotionApiTransport, expected_page: JSONMap
) -> JSONMap:
    # Given
    checkpoint: JSONMap = {"q2_status": "pending"}
    operations = NotionWriteOperations(
        transport,
        lambda _request, _operation: {},
        lambda: 0.0,
    )

    # When
    return operations.verify_q2(
        tmp_path / "notion-checkpoint.json",
        _plan(),
        checkpoint,
        "page",
        30.0,
        expected_page,
        lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("parent", "reason"),
    [
        (
            {"type": "data_source_id", "data_source_id": "other-source"},
            "does not match",
        ),
        ({"type": "database_id", "database_id": "database"}, "parent type"),
        ({"type": "data_source_id"}, "data source"),
        (None, "parent data source"),
    ],
)
def test_q2_checkpoints_failure_when_fetched_page_parent_is_not_expected_target(
    tmp_path: Path, parent: JSONMap | None, reason: str
) -> None:
    # Given
    response: JSONMap = {"properties": {"제목": {"title": [{"plain_text": "topic"}]}}}
    if parent is not None:
        response["parent"] = parent

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/query"):
            return httpx2.Response(200, json=_query_response())
        assert request.url.path == "/v1/pages/page"
        return httpx2.Response(200, json=response)

    client = create_notion_client(
        NotionApiToken("token"), transport=httpx2.MockTransport(handler)
    )
    content_hash = hashlib.sha256(b"thumbnail").hexdigest()
    transport = NotionApiTransport(
        client,
        target_id="data-source-expected",
        asset_metadata=(AssetMetadata("thumbnail", content_hash, 1),),
    )

    with client, pytest.raises(NotionQ2Failure, match=reason):
        _ = _verify_q2(tmp_path, transport, _expected_page(content_hash))
    checkpoint = json.loads((tmp_path / "notion-checkpoint.json").read_text())
    assert checkpoint["q2_status"] == "failed"


def test_q2_passes_when_fetched_page_parent_matches_expected_target(
    tmp_path: Path,
) -> None:
    # Given
    content = b"thumbnail"
    content_hash = hashlib.sha256(content).hexdigest()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/query"):
            return httpx2.Response(200, json=_query_response())
        if request.url.path == "/v1/pages/page":
            return httpx2.Response(
                200,
                json={
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": "data-source-expected",
                    },
                    "properties": {"제목": {"title": [{"plain_text": "topic"}]}},
                },
            )
        if request.url.path == "/v1/blocks/page/children":
            return httpx2.Response(
                200,
                json={
                    "results": [
                        {
                            "type": "image",
                            "image": {
                                "type": "file",
                                "file": {"url": "https://files.example/signed"},
                                "caption": [],
                            },
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx2.Response(200, content=content)

    client = create_notion_client(
        NotionApiToken("token"), transport=httpx2.MockTransport(handler)
    )
    transport = NotionApiTransport(
        client,
        target_id="data-source-expected",
        asset_metadata=(AssetMetadata("thumbnail", content_hash, 1),),
    )

    # When
    with client:
        result = _verify_q2(tmp_path, transport, _expected_page(content_hash))

    # Then
    checkpoint = json.loads((tmp_path / "notion-checkpoint.json").read_text())
    assert result["storage_integrity"] == "passed"
    assert checkpoint["q2_status"] == "passed"
